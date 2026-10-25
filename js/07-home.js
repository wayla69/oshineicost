// ─── 07-home.js — slice 7/9 of the former app.js (P2 structure refactor) ───
// Sequential load order matters: index.html loads 01→09; classic scripts
// share one global scope, so this equals the original single file.


// ════════════════════════════════════════════════════════════════
// SHARED PALETTE
// ════════════════════════════════════════════════════════════════
const PALETTE_6 = ['#4a70b0','#0891b2','#059669','#d97706','#dc2626','#7a66b8'];

// ════════════════════════════════════════════════════════════════
// HOME — Daily Brief
// ════════════════════════════════════════════════════════════════
async function loadHome() {
  const _fk = _filterKey(); if (_tabLastKey.home === _fk) return; _tabLastKey.home = _fk;
  const loading = document.getElementById('home-brief-loading');
  try { renderAdoptionBanner(); } catch(e) {}     // admins: this week's adoption digest, once per week
  try { renderTaskHealthBanner(); } catch(e) {}   // admins: a scheduled task on the office PC failed / went quiet
  const content = document.getElementById('home-brief-content');
  if (loading) loading.classList.remove('hidden');
  if (content) content.classList.add('hidden');

  try {
    const bp = new URLSearchParams();
    if (getBranch()) bp.set('branch', getBranch());
    const brief = await api(`${API}/api/home/brief?${bp}`);
    if (!brief || !brief.yesterday) {
      if (loading) loading.innerHTML = '<p class="text-sm text-slate-400 py-4">ยังไม่มีข้อมูลยอดขาย — อัปโหลดหรือซิงก์ข้อมูลก่อน</p>';
      return;
    }

    // Context fetched in parallel. Each is independent and non-fatal:
    //  · scorecard  — per-branch vs-normal drift (the core "needs attention" signal)
    //  · forecast   — month-end projection (run-rate, NOT target-based)
    //  · anomalies  — extra insight lines folded into the action queue
    //  · sd (daily) — MTD-fair MoM (weekday-weighted baseline while the month is partial) → replaces target dependency
    const sdp = new URLSearchParams(); sdp.set('gran', 'month');
    if (getBranch()) sdp.set('branch', getBranch());
    if (getGroup())  sdp.set('group', getGroup());
    const dayp = new URLSearchParams(); dayp.set('gran', 'day');     // last-7-day company trend
    if (getBranch()) dayp.set('branch', getBranch());
    if (getGroup())  dayp.set('group', getGroup());
    const gp = new URLSearchParams(bp);                              // branch (+group) for leak / movers
    if (getGroup()) gp.set('group', getGroup());
    const hp = new URLSearchParams();                               // yesterday's hourly
    hp.set('date_from', brief.yesterday); hp.set('date_to', brief.yesterday);
    if (getBranch()) hp.set('branch', getBranch());
    const [scorecard, fc, anomaly, sd, leak, movers, hourly, trend7, todo] = await Promise.all([
      renderHomeScorecard(brief.yesterday),                                       // renders leaderboard + returns its data
      api(`${API}/api/forecast?${bp}`).catch(() => null),
      api(`${API}/api/analysis/anomalies`).catch(() => ({ insights: [] })),
      api(`${API}/api/sales/daily?${sdp}`).catch(() => null),
      api(`${API}/api/home/leak-watch?${gp}`).catch(() => null),
      api(`${API}/api/home/menu-movers?${gp}&limit=15`).catch(() => null),
      api(`${API}/api/analysis/hourly?${hp}`).catch(() => null),
      api(`${API}/api/sales/daily?${dayp}`).catch(() => null),
      // วัตถุดิบและใบสั่งซื้อ: my open work from หน้างาน (16-supplyhome.js) — only for a supply role
      ['supply.ordering', 'bom.center', 'bom.edit', 'bom.release', 'bom.audit', 'branch.stock', 'ops.report', 'cost.analysis'].some(hasModule)
        ? api(`${API}/api/bom-center/todo`).catch(() => null) : Promise.resolve(null),
    ]);
    window._shmHome = todo && Array.isArray(todo.items) ? todo : null;

    // Reveal the content FIRST so the chart canvases have real layout dimensions —
    // mkChart() on a display:none canvas renders to 0×0 and never recovers.
    if (loading) loading.classList.add('hidden');
    if (content) content.classList.remove('hidden');

    renderHomeStatusBand(brief, fc, sd, scorecard);
    renderHomeDecisionTiles(brief, fc, sd, scorecard);
    renderHomeActionQueue(brief, fc, sd, scorecard, anomaly, leak, window._shmHome);
    renderHomeForecast(fc);
    renderHomeLeakWatch(leak, brief);
    renderHomeMenuMovers(movers);
    renderHomeMoversRevenue(movers);
    renderHomePeakHours(hourly, brief.yesterday);
    renderHomeTrend7(trend7);

    // External market & industry context (independent — never blocks the brief)
    loadMarket();
  } catch(e) {
    if (loading) loading.innerHTML = `<p class="text-red-500 text-sm">Failed to load daily brief: ${e.message}</p>`;
  }
}

// Shared traffic-light verdict for the Daily Brief — worst of {margin, branch drift, run-rate}.
function _homeStatus(brief, sd, scorecard) {
  const fcp = brief.food_cost_pct_mtd;
  const mom = sd && sd.total ? sd.total.mom_pct : null;
  const branches = (scorecard && scorecard.branches) || [];
  const worstDrift = branches.reduce((m, b) => (b.vs_avg_pct != null && b.vs_avg_pct < m ? b.vs_avg_pct : m), 0);
  const downCount = branches.filter(b => b.status === 'down' || (b.vs_avg_pct != null && b.vs_avg_pct <= -8)).length;
  // Food cost colours the whole page only when it is genuinely off: above THIS business's
  // one target (red; Settings → ค่าเริ่มต้น, 58%), or climbing / not yet trustworthy (amber). The old bars (45% / 38%) are
  // à-la-carte norms — for a buffet they were true every day, so the band at the top of
  // the first screen anyone opens said "ต้องรีบดู" all year and meant nothing.
  const H = _homeCostHealth(brief);
  const fcRed = fcp != null && H.above;
  const fcAmber = fcp != null && ((H.c.delta_pp != null && H.c.delta_pp >= 3) || (H.c.coverage_pct != null && H.c.coverage_pct < 95));
  let level = 'green';
  if (fcRed || worstDrift <= -15 || (mom != null && mom < -8)) level = 'red';
  else if (fcAmber || worstDrift <= -8 || (mom != null && mom < -3)) level = 'amber';
  return { level, mom, worstDrift, downCount, H, fcRed, fcAmber };
}

function renderHomeStatusBand(brief, fc, sd, scorecard) {
  const el = document.getElementById('home-status-band'); if (!el) return;
  const s = _homeStatus(brief, sd, scorecard);
  const palette = {
    green: { bg: 'rgba(16,185,129,0.08)', bd: '#10b981', word: 'ปกติดี',    wc: '#065f46' },
    amber: { bg: 'rgba(245,158,11,0.10)', bd: '#f59e0b', word: 'ต้องระวัง', wc: '#92400e' },
    red:   { bg: 'rgba(239,68,68,0.09)',  bd: '#ef4444', word: 'ต้องรีบดู', wc: '#991b1b' },
  }[s.level];
  const wow = brief.revenue_wow ? ((brief.revenue_yesterday - brief.revenue_wow) / brief.revenue_wow * 100) : null;
  const wowTxt = wow != null ? `(${wow >= 0 ? '+' : ''}${wow.toFixed(0)}% เทียบสัปดาห์ก่อน)` : '';
  const projTxt = fc && fc.available ? ` คาดจบเดือน ~<b>${fmt(fc.projected)}</b>` : '';
  const skipNote = brief.skipped_day ? ` · <span style="color:var(--text-muted)">ข้อมูลวันที่ ${brief.skipped_day} มีเพียง ${brief.skipped_bills} บิล ยังมาไม่ครบ จึงแสดงวันก่อนหน้า</span>` : '';
  const line1 = `เมื่อวาน (${fmtDateTh(brief.yesterday)}) ทำได้ <b>${fmt(brief.revenue_yesterday || 0)}</b> ${wowTxt} · เดือนนี้ทำไป <b>${fmt(brief.mtd_revenue || 0)}</b> ใน ${brief.mtd_days || 0} วัน${projTxt}${skipNote}`;
  const bits = [];
  if (s.downCount) bits.push(`${s.downCount} สาขาต่ำกว่าปกติ`);
  if (s.fcRed) bits.push(`ต้นทุนอาหาร ${brief.food_cost_pct_mtd.toFixed(1)}% ${fcStatusText(brief.food_cost_pct_mtd, s.H.t)} (เป้า ${fcTargetTxt(s.H.t)}%)`);
  else if (s.fcAmber && s.H.c.delta_pp >= 3) bits.push(`ต้นทุนอาหารขึ้น ${s.H.c.delta_pp.toFixed(1)} จุด เทียบช่วงเดียวกันเดือนก่อน`);
  else if (s.fcAmber) bits.push(`ต้นทุนครบแค่ ${s.H.c.coverage_pct.toFixed(0)}% ของจาน`);
  if (sd && sd.total && sd.total.mom_pct != null && sd.total.mom_pct < -3) bits.push(`ยอดขายเดือนนี้ต่ำกว่าช่วงเดียวกันของเดือนก่อน ${Math.abs(sd.total.mom_pct).toFixed(0)}%`);
  const line2 = bits.length ? bits.join(' · ') : 'ทุกตัวชี้วัดอยู่ในเกณฑ์ปกติ';
  el.innerHTML = `<div class="flex items-center gap-3 p-4 rounded-xl" style="background:${palette.bg};border:1px solid ${palette.bd}33">
    <span style="width:11px;height:11px;border-radius:50%;background:${palette.bd};flex-shrink:0"></span>
    <div class="flex-1 min-w-0">
      <p class="text-sm" style="color:var(--text-primary)">${line1}</p>
      <p class="text-xs mt-1" style="color:var(--text-secondary)">${line2}</p>
    </div>
    <span class="text-xs px-3 py-1 rounded-lg whitespace-nowrap" style="background:var(--bg-card);color:${textInk(palette.wc)};font-weight:600">${palette.word}</span>
  </div>`;
}

// One reading of brief.cost_health for the tile AND the action queue, so the two can
// never disagree about whether food cost is a problem today.
function _homeCostHealth(brief) {
  const c = (brief && brief.cost_health) || {};
  const fcp = brief ? brief.food_cost_pct_mtd : null;
  const t = fcTargetSet(c.food_cost_target);   // ONE target (owner 2026-10-25): ≤ target ในเป้า, above it เกินเป้า x.x จุด
  const above = fcp != null && fcp > t;
  const d = c.delta_pp, moved = d != null && Math.abs(d) >= 2;
  const lowCov = c.coverage_pct != null && c.coverage_pct < 98;
  const color = fcp == null ? '#94a3b8' : above ? '#ef4444' : '#10b981';
  const cov = c.coverage_pct != null ? ` · ต้นทุนครบ ${c.coverage_pct.toFixed(1)}% ของจาน` : '';
  let foot, footColor;
  if (lowCov && c.est_food_pct != null) {
    foot = `ตัวเลขนี้เป็นค่าต่ำสุด — รวมจานที่ยังไม่มีต้นทุนแล้วราว ${c.est_food_pct.toFixed(1)}%`; footColor = '#d97706';
  } else if (above) {
    foot = `${fcStatusText(fcp, t)}${moved ? ` · ${d > 0 ? '▲' : '▼'} ${Math.abs(d).toFixed(1)} จุด เทียบช่วงเดียวกันเดือนก่อน` : ''}`; footColor = '#dc2626';
  } else if (moved) {
    foot = `${d > 0 ? '▲' : '▼'} ${Math.abs(d).toFixed(1)} จุด เทียบช่วงเดียวกันเดือนก่อน · ในเป้า`;
    footColor = d > 0 ? '#d97706' : '#059669';
  } else {
    foot = 'ในเป้า · นิ่งเทียบช่วงเดียวกันเดือนก่อน';
    footColor = '#059669';
  }
  return { c, t, above, moved, lowCov, color, foot, footColor, sub: `เป้า ${fcTargetTxt(t)}%${cov}` };
}

function renderHomeDecisionTiles(brief, fc, sd, scorecard) {
  const el = document.getElementById('home-decision-tiles'); if (!el) return;
  const branches = (scorecard && scorecard.branches) || [];
  const down = branches.filter(b => b.status === 'down' || (b.vs_avg_pct != null && b.vs_avg_pct <= -8))
                       .sort((a, b) => (a.vs_avg_pct ?? 0) - (b.vs_avg_pct ?? 0));
  const mom = sd && sd.total ? sd.total.mom_pct : null;
  const momColor = mom == null ? '#64748b' : mom >= 0 ? '#059669' : mom <= -5 ? '#dc2626' : '#f59e0b';
  // Food cost is judged against THIS business's one target (Settings → ค่าเริ่มต้น; 58%, owner
  // 2026-10-25), not the à-la-carte 38/45 this tile used to hard-code — that left it red
  // every day and taught everyone to ignore it. What earns colour now: outside the band,
  // or a real move against last month. And the figure says how complete its costing is.
  const fcp = brief.food_cost_pct_mtd;
  const H = _homeCostHealth(brief);
  const tiles = [
    {
      icon: '', label: 'จังหวะเดือนนี้',
      value: fc && fc.available ? fmt(fc.projected) : (brief.mtd_revenue ? fmt(brief.mtd_revenue) : '—'),
      sub: fc && fc.available ? `วิ่ง ${fmt(fc.avg_daily)}/วัน · ${fc.days_elapsed}/${fc.days_total} วัน` : 'คาดการณ์ไม่ได้',
      // PR-PP2: เดือนนี้ to date vs days 1..n of last month and of the month a year back (the two chips)
      footHtml: sd && sd.total ? periodCmpHtml(sd.total.revenue, sd.total.prev && sd.total.prev.revenue, sd.total.ly && sd.total.ly.revenue, { range: periodOf(sd) }) : '',
      footColor: momColor, valColor: '#4a70b0', go: "switchTab('dailysales')",
    },
    {
      icon: '', label: 'สาขาที่ต้องดู',
      value: down.length ? `${down.length} สาขา` : 'ครบทุกสาขา',
      sub: down.length ? 'ต่ำกว่าค่าปกติของตัวเอง' : 'ไม่มีสาขาต่ำกว่าปกติ',
      foot: down.length ? down.slice(0, 2).map(b => `${esc(b.branch)} ${b.vs_avg_pct != null ? b.vs_avg_pct.toFixed(0) + '%' : ''}`).join(' · ') : 'ทุกสาขาปกติ',
      footColor: down.length ? '#dc2626' : '#059669', valColor: down.length ? '#dc2626' : '#059669',
      go: down.length ? `homeDrillBranch('${(down[0].branch || '').replace(/'/g, "\\'")}')` : '',
    },
    {
      icon: '', label: 'สุขภาพมาร์จิ้น (ต้นทุนอาหาร %)',
      value: fcp != null ? `${fcp.toFixed(1)}%` : '—',
      sub: fcp != null ? H.sub : 'ยังไม่มีข้อมูลต้นทุน',
      foot: fcp != null ? H.foot : '',
      // PR-PP3: the month to date against the same days of last month and of the month a year back (จุด; down = good)
      footHtml: (fcp != null && H.c.period) ? periodCmpHtml(fcp, H.c.prev_food_pct, H.c.ly_food_pct, { unit: 'pp', upGood: false, range: H.c.period }) : '',
      footColor: H.footColor, valColor: H.color, go: "switchTab('costhead')",
    },
  ];
  if (!canSeeCost()) tiles.pop();    // PL3: the margin tile (ต้นทุนอาหาร %) is only for the roles that hold a cost or profit module
  el.innerHTML = tiles.map(t => `
    <div class="card p-4 ${t.go ? 'cursor-pointer' : ''}" ${t.go ? `onclick="${t.go}"` : ''} style="${t.go ? 'transition:box-shadow .15s' : ''}">
      <div class="flex items-center gap-2 mb-2">
        <span>${t.icon}</span>
        <span class="text-xs" style="color:var(--text-muted)">${t.label}</span>
      </div>
      <p class="text-2xl font-bold" style="color:${textInk(t.valColor)}">${t.value}</p>
      <p class="text-[11px] mt-0.5" style="color:var(--text-muted)">${t.sub}</p>
      ${t.footHtml ? `<div class="mt-1.5" onclick="event.stopPropagation()">${t.footHtml}</div>` : ''}
      ${t.foot ? `<p class="text-[11px] mt-1.5 font-medium" style="color:${textInk(t.footColor)}">${t.foot}</p>` : ''}
    </div>`).join('');
}

// Jump to a branch's daily-sales drill from a Home card/action.
function homeDrillBranch(name) {
  const sel = document.getElementById('selBranch');
  if (sel) sel.value = name;
  switchTab('dailysales');
}

// Action queue — synthesises margin + branch drift + run-rate + anomalies into a
// prioritised to-do list (red → amber → green). This is the Daily Brief's unique job.
function renderHomeActionQueue(brief, fc, sd, scorecard, anomaly, leak, todo) {
  const el = document.getElementById('home-action-queue'); if (!el) return;
  const branches = (scorecard && scorecard.branches) || [];
  const A = [];
  // Money-leak signals first — these are the things an owner can't eyeball daily.
  if (leak) {
    const ch = leak.cash_heavy || {};
    if (ch.bills) A.push({ p: 0, sev: 'red', icon: '', text: `เงินสด+ลดหนัก ${ch.bills} บิล (฿${fmtN(ch.baht || 0)}) เมื่อวาน — เสี่ยงเงินรั่ว ตรวจบิล`, act: 'ดูส่วนลด', go: "switchTab('discounts')" });
    const hd = leak.heavy_discount || {};
    if (hd.bills >= 5 && !ch.bills) A.push({ p: 2, sev: 'amber', icon: '', text: `ส่วนลดหนัก ≥30% ${hd.bills} บิล (฿${fmtN(hd.baht || 0)}) เมื่อวาน — ตรวจเหตุผลการลด`, act: 'ดูส่วนลด', go: "switchTab('discounts')" });
    const rp = leak.reprinted || {};
    if (rp.bills) A.push({ p: 2, sev: 'amber', icon: '', text: `บิลมีส่วนลดถูกพิมพ์ซ้ำ ${rp.bills} บิล เมื่อวาน — ตรวจการพิมพ์ซ้ำ`, act: 'ดูส่วนลด', go: "switchTab('discounts')" });
  }
  // ต้นทุนอาหาร: fires on what is actually wrong TODAY — outside this business's band, a
  // real move against last month, or a figure that cannot be trusted yet. The old rule
  // (> 45%) was true every day of the year for a buffet, so it was the permanent #1
  // "urgent" item and pushed the real ones down.
  const fcp = canSeeCost() ? brief.food_cost_pct_mtd : null;   // PL3: no cost sentences for a role without a cost or profit module
  const H = _homeCostHealth(brief), CH = canSeeCost() ? H.c : {};
  if (fcp != null && H.above)
    A.push({ p: 0, sev: 'red', icon: '', text: `ต้นทุนอาหารเดือนนี้ ${fcp.toFixed(1)}% — เป้า ${fcTargetTxt(H.t)}% · ${fcStatusText(fcp, H.t)}${H.moved && CH.delta_pp > 0 ? ` และขึ้นมา ${CH.delta_pp.toFixed(1)} จุด เทียบช่วงเดียวกันเดือนก่อน` : ''}`, act: 'ดูต้นทุน/หัว', go: "switchTab('costhead')" });
  else if (fcp != null && CH.delta_pp != null && CH.delta_pp >= 3)
    A.push({ p: 2, sev: 'amber', icon: '', text: `ต้นทุนอาหารขยับขึ้น ${CH.delta_pp.toFixed(1)} จุด เทียบช่วงเดียวกันเดือนก่อน (${(CH.prev_food_pct ?? 0).toFixed(1)}% → ตอนนี้) — ยังในเป้า ${fcTargetTxt(H.t)}% แต่ควรดูว่าเมนูไหนดัน`, act: 'ดูต้นทุน/หัว', go: "switchTab('costhead')" });
  if (CH.coverage_pct != null && CH.coverage_pct < 95)
    A.push({ p: 3, sev: 'amber', icon: '', text: `จานที่เสิร์ฟ ${(100 - CH.coverage_pct).toFixed(1)}% ยังไม่มีต้นทุน (${fmtN(CH.uncosted_plates || 0)} จาน) — Food% ที่เห็นต่ำกว่าจริง`, act: 'เติมต้นทุน', go: (BC_MODE === 'stock' && tabAllowed('bomcenter')) ? "supplyGo({tab:'bomcenter',pane:'dishcost'})" : "switchTab('coverage')" });   // PL1: stock mode fills the cost on the ต้นทุนต่อจาน page
  // the requisition-against-recipe items belong to the recipe pages, which stock mode does not offer (PL1 #4)
  const RQ = BC_MODE === 'stock' ? null : CH.requisition;
  if (RQ && RQ.worst && RQ.worst.ratio >= 1.25)
    A.push({ p: 3, sev: 'amber', icon: '', text: `${esc(RQ.worst.branch)} เบิกวัตถุดิบสูงกว่าต้นทุนตามสูตร ${RQ.worst.ratio.toFixed(2)} เท่า (${esc(RQ.worst.month)}) — ตักเกินสูตร ของเสีย หรือตุนสต๊อก`, act: 'ดูเบิก vs สูตร', go: "switchTab('branchbom')" });
  if (RQ && RQ.missing_month)
    A.push({ p: 6, sev: 'amber', icon: '', text: `ยังไม่ได้อัปโหลดใบเบิกวัตถุดิบเดือน ${esc(RQ.missing_month)} — รายงานเบิกจริง vs สูตรจึงยังไม่มีเดือนนั้น`, act: 'อัปโหลด', go: "switchTab('branchbom')" });
  if (CH.pending_review && CH.pending_review.count)
    A.push({ p: 6, sev: 'amber', icon: '', text: `${CH.pending_review.count} เมนูใช้ต้นทุนที่ระบบประมาณให้ (฿${fmtN(CH.pending_review.cogs_month || 0)}/เดือน) — รอครัวตรวจปริมาณแล้วกดยืนยัน`, act: 'ตรวจและยืนยัน', go: "switchTab('coverage')" });
  const down = branches.filter(b => b.status === 'down' || (b.vs_avg_pct != null && b.vs_avg_pct <= -8))
                       .sort((a, b) => (a.vs_avg_pct ?? 0) - (b.vs_avg_pct ?? 0));
  down.slice(0, 3).forEach(b => {
    const big = b.vs_avg_pct != null && b.vs_avg_pct <= -15;
    const wowBit = b.vs_wow_pct != null ? ` · เทียบสัปดาห์ก่อน ${b.vs_wow_pct >= 0 ? '+' : ''}${b.vs_wow_pct.toFixed(0)}%` : '';
    A.push({ p: big ? 0 : 2, sev: big ? 'red' : 'amber', icon: '',
      text: `${esc(b.branch)} ต่ำกว่าค่าปกติ ${b.vs_avg_pct != null ? Math.abs(b.vs_avg_pct).toFixed(0) + '%' : ''}${wowBit} — เจาะดูสาเหตุ`,
      act: 'เจาะสาขา', go: `homeDrillBranch('${(b.branch || '').replace(/'/g, "\\'")}')` });
  });
  if (sd && sd.total && sd.total.mom_pct != null && sd.total.mom_pct < -3)
    A.push({ p: 3, sev: 'amber', icon: '▼', text: `เดือนนี้ยอดขายต่ำกว่าช่วงเดียวกันของเดือนก่อน ${Math.abs(sd.total.mom_pct).toFixed(0)}% (${(periodOf(sd) || {}).label_th || ''} เทียบ ${(periodOf(sd) || {}).prev_label_th || ''})`, act: 'ดูยอดรายวัน', go: "switchTab('dailysales')" });
  // /api/analysis/anomalies scans the WHOLE history (its own panel wants that), and its
  // BoM-cost rule flags every month that ever jumped — so a spike from June 2025 sat in
  // "สิ่งที่ต้องทำวันนี้" in September 2026. Today's list takes only what is still news:
  // this month and the two before it. An insight with no period is kept — it cannot be
  // judged stale.
  const _y = String(brief.yesterday || '').slice(0, 7);
  const _cut = (() => {
    if (!/^\d{4}-\d{2}$/.test(_y)) return '';
    const n = (+_y.slice(0, 4)) * 12 + (+_y.slice(5, 7)) - 1 - 2;
    return `${Math.floor(n / 12)}-${String(n % 12 + 1).padStart(2, '0')}`;
  })();
  ((anomaly && anomaly.insights) || []).filter(a => a.severity === 'bad')
    .filter(a => !a.period || !_cut || String(a.period).slice(0, 7) >= _cut).slice(0, 3)
    .forEach(a => A.push({ p: 4, sev: 'amber', icon: '⚠', text: esc(String(a.message || '').replace(/^\s*⚠\s*/, '')) }));   // the message may carry its own ⚠
  const up = branches.filter(b => b.status === 'up' && b.vs_avg_pct != null).sort((a, b) => b.vs_avg_pct - a.vs_avg_pct)[0];
  if (up && up.vs_avg_pct >= 10)
    A.push({ p: 9, sev: 'green', icon: '▲', text: `${esc(up.branch)} เหนือปกติ +${up.vs_avg_pct.toFixed(0)}% — ดูว่าทำอะไรดีแล้วขยายผล`, act: 'เจาะสาขา', go: `homeDrillBranch('${(up.branch || '').replace(/'/g, "\\'")}')` });
  // วัตถุดิบและใบสั่งซื้อ: at most three of my open items from หน้างาน, overdue ones first
  if (todo && Array.isArray(todo.items)) {
    const isMine = i => typeof _shmMine === 'function' ? _shmMine(i) : (i.owner_modules || []).some(hasModule);
    const mine = todo.items.filter(i => (i.status === 'todo' || i.status === 'blocked') && !i.upcoming && isMine(i));
    mine.sort((a, b) => ((b.urgent ? 1 : 0) - (a.urgent ? 1 : 0)) || (a.seq - b.seq));
    // UX batch A (2026-10-20): the same duty for several branches is ONE row naming the branches, not a row each
    const groups = [];
    mine.forEach(i => {
      const k = String(i.key || '').split(':')[0] + '|' + (i.title || '');
      const g = groups.find(x => x.k === k);
      if (g) { if (i.branch && !g.branches.includes(i.branch)) g.branches.push(i.branch); g.urgent = g.urgent || !!i.urgent; }
      else groups.push({ k, first: i, branches: i.branch ? [i.branch] : [], urgent: !!i.urgent });
    });
    groups.slice(0, 3).forEach(g => {
      const i = g.first, n = g.branches.length;
      const who = n > 1 ? ` · ${n} สาขา: ${g.branches.slice(0, 4).map(esc).join(', ')}${n > 4 ? ` และอีก ${n - 4}` : ''}` : (n ? ` (${esc(g.branches[0])})` : '');
      A.push({ p: g.urgent ? 1 : 5, sev: g.urgent ? 'red' : 'amber', icon: '',
        text: `${esc(i.title)}${who}${i.detail ? `<div style="font-size:13px;color:var(--text-secondary)">${esc(i.detail)}</div>` : ''}`,
        act: 'ไปที่งาน', go: `shmGoFromHome('${esc(i.key)}')` });
    });
  }
  A.sort((a, b) => a.p - b.p);

  const cnt = document.getElementById('home-action-count');
  const reds = A.filter(a => a.sev === 'red').length, ambers = A.filter(a => a.sev === 'amber').length;
  if (cnt) cnt.textContent = A.length ? `${reds} เร่งด่วน · ${ambers} เฝ้าระวัง` : '';
  if (!A.length) {
    el.innerHTML = `<div class="flex items-center gap-2 p-3 rounded-lg" style="background:rgba(16,185,129,0.07)"><span>✓</span><span class="text-sm" style="color:var(--text-primary)">ทุกอย่างอยู่ในเกณฑ์ปกติ ไม่มีเรื่องเร่งด่วนวันนี้</span></div>`;
    return;
  }
  const sevc = { red: { a: '#ef4444', t: 'rgba(239,68,68,0.06)' }, amber: { a: '#f59e0b', t: 'rgba(245,158,11,0.07)' }, green: { a: '#10b981', t: 'rgba(16,185,129,0.06)' } };
  // UX batch A: a neutral card with a coloured bar at the left; only red keeps its tint, so the urgent rows stand out
  el.innerHTML = A.slice(0, 8).map(a => {
    const c = sevc[a.sev];
    return `<div class="flex items-center gap-3 p-3 rounded-lg" style="background:${a.sev === 'red' ? c.t : 'var(--bg-card)'};border:1px solid var(--border);border-left:4px solid ${c.a}">
      <span class="text-base">${a.icon}</span>
      <span class="flex-1 text-sm" style="color:var(--text-primary)">${a.text}</span>
      ${a.go ? `<button onclick="${a.go}" class="text-xs px-2.5 py-1 rounded-md whitespace-nowrap" style="border:1px solid var(--border);color:var(--text-secondary);background:var(--bg-card)">${a.act || 'ดู'} →</button>` : ''}
    </div>`;
  }).join('');
}

// Month-end daily pace (run-rate projection, not target-based).
function renderHomeForecast(fc) {
  const per = document.getElementById('home-forecast-period');
  if (!fc || !fc.available) { if (per) per.textContent = 'ไม่มีข้อมูลคาดการณ์'; return; }
  if (per) per.textContent = `${typeof fmtMonth === 'function' ? fmtMonth(fc.period) : fc.period} · วันที่ ${fc.days_elapsed} จาก ${fc.days_total}`;
  const prog = Math.round(fc.days_elapsed / fc.days_total * 100);
  document.getElementById('home-forecast-kpis').innerHTML = [
    { label: 'ตั้งแต่ต้นเดือน', value: fmt(fc.mtd), color: '#4a70b0' },
    { label: 'คาดจบเดือน', value: fmt(fc.projected), color: 'var(--good-ink)' },
    { label: 'ความคืบหน้า', value: `${prog}%`, color: '#0891b2' },
  ].map(k => `<div class="text-center"><p class="text-xs text-slate-500">${k.label}</p><p class="text-base font-bold" style="color:${textInk(k.color)}">${k.value}</p></div>`).join('');
  const dailyLabels = fc.daily.map(d => d.day.slice(5));
  const dailyData   = fc.daily.map(d => d.revenue);
  const remain      = fc.days_total - fc.days_elapsed;
  const projLabels  = Array.from({ length: remain }, (_, i) => `+${i + 1}`);
  mkChart('chartHomeForecast', {
    type: 'bar',
    data: {
      labels: [...dailyLabels, ...projLabels],
      datasets: [
        { label: 'จริง', data: [...dailyData, ...Array(remain).fill(null)],
          backgroundColor: '#4a70b022', borderColor: '#4a70b0', borderWidth: 1.5, borderRadius: 2 },
        { label: 'คาดการณ์', data: [...Array(dailyData.length).fill(null), ...Array(remain).fill(fc.avg_daily)],
          type: 'line', borderColor: '#059669', borderDash: [4, 3], borderWidth: 2,
          pointRadius: 0, fill: false, tension: 0 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } }, datalabels: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => fmt(v) } } }
    }
  });
}

// Daily money-leak radar — heavy discount / cash+heavy / reprinted / zero-gross.
function renderHomeLeakWatch(leak, brief) {
  const el = document.getElementById('home-leak-watch'); if (!el) return;
  const dl = document.getElementById('home-leak-date');
  if (dl) dl.textContent = leak && leak.date ? `· ${fmtDateTh(leak.date)}` : '';
  if (!leak) { el.innerHTML = '<p class="text-xs text-slate-400 py-2">โหลดไม่สำเร็จ</p>'; return; }
  const cards = [
    { key: 'cash_heavy',     label: 'เงินสด + ลดหนัก', hint: 'เสี่ยงเงินรั่ว', danger: true },
    { key: 'heavy_discount', label: 'ส่วนลดหนัก ≥30%',  hint: 'ตรวจเหตุผล',   danger: false },
    { key: 'reprinted',      label: 'บิลพิมพ์ซ้ำ',      hint: 'เสี่ยงพิมพ์ซ้ำ', danger: false },
    { key: 'zero_gross',     label: 'บิลลดล้วน (ยอด 0)', hint: 'ตรวจบิล',      danger: false },
  ];
  // Reconciling total: ALL discounted bills for the day (cards below are the abnormal
  // subsets). Shown even when nothing is flagged, so the day's discount still ties out
  // with the Discounts page.
  const lt = leak.total || { bills: 0, baht: 0 };
  const totalBanner = `<div class="flex items-center justify-between mb-2.5 px-1">
      <span class="text-[11px]" style="color:var(--text-muted)">ส่วนลดรวมทั้งวัน <span style="opacity:.7">(ทุกบิล · ตรงกับหน้า Discounts)</span></span>
      <span class="text-sm font-bold" style="color:var(--text-primary)">฿${fmtN(lt.baht || 0)} <span class="text-[11px] font-normal" style="color:var(--text-muted)">· ${fmtN(lt.bills || 0)} บิล</span></span>
    </div>`;
  // Given away this month. Not a discount, so it never showed anywhere: a free dish
  // books no revenue and — until it was costed on 2026-09-18 — no cost either.
  const gv = brief && brief.cost_health && brief.cost_health.giveaways;
  const giveBanner = gv && gv.value ? `<div class="flex items-center justify-between mb-2.5 px-2 py-2 rounded-lg cursor-pointer" style="background:rgba(217,119,6,0.07)" onclick="switchTab('coverage')" title="${gv.top.map(x => esc(x.name) + ' ' + fmtN(x.plates) + ' จาน').join(' · ')}">
      <span class="text-[11px]" style="color:var(--text-muted)">ของแจกฟรี/แถม เดือนนี้ <span style="opacity:.7">(ต้นทุนจริง · ${fmtN(gv.plates)} จาน${gv.top[0] ? ' · มากสุด ' + esc(gv.top[0].name) : ''})</span></span>
      <span class="text-sm font-bold" style="color:var(--amber-ink)">฿${fmtN(gv.value)}</span>
    </div>` : '';
  const anyHit = cards.some(c => (leak[c.key] || {}).bills);
  if (!anyHit) {
    el.innerHTML = totalBanner + giveBanner + `<div class="flex items-center gap-2 p-3 rounded-lg" style="background:rgba(16,185,129,0.07)"><span>✓</span><span class="text-sm" style="color:var(--text-primary)">ไม่พบรายการผิดปกติเมื่อวาน</span></div>`;
    return;
  }
  el.innerHTML = totalBanner + giveBanner + `<div class="grid grid-cols-2 gap-2.5">` + cards.map(c => {
    const v = leak[c.key] || { bills: 0, baht: 0 };
    const hot = v.bills > 0;
    const col = !hot ? '#94a3b8' : c.danger ? 'var(--bad-ink)' : '#d97706';
    const bg  = !hot ? 'var(--bg-subtle)' : c.danger ? 'rgba(239,68,68,0.07)' : 'rgba(245,158,11,0.07)';
    const branchHint = hot && v.by_branch && v.by_branch.length
      ? esc(v.by_branch[0].branch) + (v.by_branch.length > 1 ? ` +${v.by_branch.length - 1}` : '') : c.hint;
    return `<div class="p-3 rounded-lg ${hot ? 'cursor-pointer' : ''}" style="background:${bg}" ${hot ? `onclick="switchTab('discounts')"` : ''}>
      <p class="text-[11px]" style="color:var(--text-muted)">${c.label}</p>
      <p class="text-xl font-bold" style="color:${textInk(col)}">${fmtN(v.bills)} <span class="text-xs font-normal">บิล</span></p>
      <p class="text-[11px]" style="color:var(--text-muted)">${v.baht ? '฿' + fmtN(v.baht) + ' · ' : ''}${branchHint}</p>
    </div>`;
  }).join('') + `</div>`;
}

// Yesterday's biggest menu movers — UP and DOWN, ranked by plates changed (impact).
function renderHomeMenuMovers(m) {
  const el = document.getElementById('home-menu-movers'); if (!el) return;
  if (!m) { el.innerHTML = '<p class="text-xs text-slate-400 py-2">โหลดไม่สำเร็จ</p>'; return; }
  const up = m.up || [], down = m.down || [];
  if (!up.length && !down.length) {
    el.innerHTML = '<p class="text-xs text-slate-400 py-3 italic">ไม่มีเมนูที่เคลื่อนไหวเด่นชัดเมื่อวาน (ตามเกณฑ์ impact)</p>';
    return;
  }
  const row = (x, isUp) => {
    const col = isUp ? 'var(--good-ink)' : 'var(--bad-ink)';
    const arrow = isUp ? '▲' : '▼';
    const tag = x.is_new ? '<span class="text-[10px] px-1 rounded" style="background:#dcfce7;color:#166534">ใหม่</span> ' : '';
    const d = x.delta_ph100;
    const main = `${arrow} ${d >= 0 ? '+' : ''}${d} จาน/100หัว`;
    const ctx = x.is_new
      ? `${fmtN(x.today)} จานวันนี้`
      : `วันนี้ ${x.ph100} vs ปกติ ${x.avg_ph100}${x.pct != null ? ` · ${x.pct >= 0 ? '+' : ''}${x.pct}%` : ''}`;
    // Revenue tie-in: ฿ change vs the trailing avg day for this menu — shows whether a
    // per-head move actually moved money. Colored to match the direction.
    // Buffet dishes carry NO line revenue (they ride the package — see the (B)
    // convention), so a literal "฿+0" on every row read as broken data. Only show
    // the ฿ chip when this menu actually moved money.
    const revTxt = (x.rev_delta)
      ? ` · <span style="color:${textInk(col)};font-weight:600">฿${x.rev_delta >= 0 ? '+' : ''}${fmtN(x.rev_delta)}</span>`
      : '';
    // An un-named SKU (description still equals the POS code — a new-branch code
    // series the sync hasn't backfilled) is labelled honestly instead of showing
    // a cryptic code as if it were a dish name.
    const noName = /^[A-Z0-9][A-Z0-9-]{4,}$/.test(x.menu || '');
    const nameHtml = noName
      ? `<span style="opacity:.85">${esc(x.menu)}</span> <span class="text-[10px]" style="color:var(--text-muted)">(ยังไม่มีชื่อเมนู)</span>`
      : esc(x.menu);
    // Two-line row: name + headline delta on line 1 (name truncates via min-w-0 so
    // it yields space and never collides with the nowrap delta), muted context on
    // line 2. Fixes the overlap from cramming name + delta + context on one line.
    return `<div class="py-1 border-b" style="border-color:var(--border)">
      <div class="flex items-baseline justify-between gap-2">
        <span class="text-xs font-medium truncate min-w-0" style="color:var(--text-primary)" title="${esc(x.menu)}">${tag}${nameHtml}</span>
        <span class="text-xs font-semibold whitespace-nowrap tabular-nums" style="color:${textInk(col)}">${main}</span>
      </div>
      <div class="text-[10px] tabular-nums" style="color:var(--text-muted)">${ctx}${revTxt}</div>
    </div>`;
  };
  el.innerHTML = `<div class="grid grid-cols-1 md:grid-cols-2 gap-x-5 gap-y-1">
    <div>
      <p class="text-[11px] font-semibold uppercase tracking-wide mb-1" style="color:var(--good-ink)">▲ ขายพุ่ง <span style="color:var(--text-muted)">(${up.length})</span></p>
      <div style="max-height:360px;overflow-y:auto;padding-right:4px">
        ${up.length ? up.map(x => row(x, true)).join('') : '<p class="text-xs text-slate-400 italic py-1">—</p>'}
      </div>
    </div>
    <div>
      <p class="text-[11px] font-semibold uppercase tracking-wide mb-1" style="color:var(--bad-ink)">▼ ขายตก <span style="color:var(--text-muted)">(${down.length})</span></p>
      <div style="max-height:360px;overflow-y:auto;padding-right:4px">
        ${down.length ? down.map(x => row(x, false)).join('') : '<p class="text-xs text-slate-400 italic py-1">—</p>'}
      </div>
    </div>
  </div>`;
}

// Movers ↔ sales: do the dish movements line up with how the BUFFET PACKAGE (the
// real revenue driver) sold, which price-tier moved, and the à-la-carte trend.
function renderHomeMoversRevenue(m) {
  const el = document.getElementById('home-movers-revenue'); if (!el) return;
  const s = m && m.sales, b = s && s.buffet;
  if (!s || !b) { el.innerHTML = '<p class="text-xs text-slate-400 py-3 italic">ยังไม่มีข้อมูลยอดขายพอจะวิเคราะห์</p>'; return; }
  const pct = (t, a) => (a ? Math.round((t - a) / a * 100) : null);
  const sgn = v => (v == null ? '—' : (v >= 0 ? '+' : '') + v + '%');
  const col = v => (v == null ? 'var(--text-muted)' : (v >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'));
  const covP = pct(b.covers_today, b.covers_avg);   // buffet covers vs avg
  const revP = pct(b.rev_today, b.rev_avg);          // buffet revenue vs avg
  const ppcP = pct(b.ppc_today, b.ppc_avg);          // avg ฿/cover = price-tier proxy
  const alaP = pct(s.alacarte_today, s.alacarte_avg);
  const upN = (m.up || []).length, downN = (m.down || []).length;
  const dishDir = upN > downN ? 'พุ่ง' : (downN > upN ? 'ตก' : 'ทรงตัว');
  const box = (label, val, c, sub) => `
    <div class="rounded-lg p-3" style="border:1px solid var(--border);background:var(--bg-input)">
      <div class="text-[11px] mb-0.5" style="color:var(--text-muted)">${label}</div>
      <div class="text-lg font-bold tabular-nums" style="color:${textInk(c)}">${val}</div>
      ${sub ? `<div class="text-[10px] mt-0.5" style="color:var(--text-muted)">${sub}</div>` : ''}
    </div>`;
  // ── narrative: align dish movement with buffet package + price tier + à-la-carte ──
  const lines = [];
  if (revP != null) {
    const dir = revP >= 0 ? 'โตขึ้น' : 'ลดลง';
    lines.push(`ยอด<b>บุฟเฟต์</b>เมื่อวาน${dir} <b style="color:${textInk(col(revP))}">${sgn(revP)}</b> (โต๊ะ ${sgn(covP)} · เทียบเฉลี่ย 21 วัน) — เป็นรายได้หลักของร้าน`);
  }
  if (ppcP != null) {
    lines.push(ppcP >= 0
      ? `ราคาเฉลี่ย/หัว <b style="color:var(--good-ink)">${sgn(ppcP)}</b> → ลูกค้าเลือก<b>ชุดราคาสูงขึ้น</b> (มื้อเย็น/พรีเมียมมากขึ้น)`
      : `ราคาเฉลี่ย/หัว <b style="color:var(--bad-ink)">${sgn(ppcP)}</b> → mix เอียงไป<b>ชุดราคาถูกลง</b> (กลางวัน/เด็กมากขึ้น)`);
  }
  // do the per-head dish movers go the SAME way as buffet covers?
  if (covP != null) {
    const same = (covP >= 0 && upN >= downN) || (covP < 0 && downN >= upN);
    lines.push(same
      ? `เมนูที่กินต่อหัว<b>${dishDir}</b> <b style="color:var(--good-ink)">สอดคล้อง</b>กับทิศทางโต๊ะบุฟเฟต์ — ลูกค้ามากขึ้นก็กินเมนูเด่นมากขึ้นตามคาด`
      : `เมนูที่กินต่อหัว<b>${dishDir}</b> <b style="color:var(--amber-ink)">สวนทาง</b>กับโต๊ะบุฟเฟต์ — การเปลี่ยนเป็นเรื่องรสนิยม/ความนิยมเมนู ไม่ใช่แค่จำนวนลูกค้า`);
  }
  if (alaP != null)
    lines.push(`เมนู<b>à-la-carte</b> (สั่งแยกจ่ายเพิ่ม) ${alaP >= 0 ? 'โต' : 'หด'} <b style="color:${textInk(col(alaP))}">${sgn(alaP)}</b> — ส่วนเสริมรายได้นอกบุฟเฟต์`);
  el.innerHTML = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
      ${box('โต๊ะบุฟเฟต์เมื่อวาน', fmtN(b.covers_today), col(covP), `เฉลี่ย ${fmtN(b.covers_avg)} · <span style="color:${textInk(col(covP))}">${sgn(covP)}</span>`)}
      ${box('รายได้บุฟเฟต์', '฿' + fmtN(b.rev_today), col(revP), `เฉลี่ย ฿${fmtN(b.rev_avg)} · <span style="color:${textInk(col(revP))}">${sgn(revP)}</span>`)}
      ${box('ราคาเฉลี่ย/หัว (ชุดราคา)', '฿' + fmtN(b.ppc_today), col(ppcP), `เฉลี่ย ฿${fmtN(b.ppc_avg)} · <span style="color:${textInk(col(ppcP))}">${sgn(ppcP)}</span>`)}
      ${box('à-la-carte อาหาร', '฿' + fmtN(s.alacarte_today), col(alaP), `เฉลี่ย ฿${fmtN(s.alacarte_avg)} · <span style="color:${textInk(col(alaP))}">${sgn(alaP)}</span>`)}
    </div>
    <div class="text-xs rounded-lg p-3 space-y-1" style="background:var(--bg-subtle,rgba(0,0,0,0.03));color:var(--text-primary)">
      ${lines.map(t => `<div>• ${t}</div>`).join('')}
    </div>`;
}

// Menu Analysis tab: narrative tying menu movements to what actually sold —
// buffet package direction, price-tier shift, à-la-carte trend, and top movers.
async function renderMenuSalesNarrative() {
  const el = document.getElementById('menuSalesNarrative'); if (!el) return;
  el.innerHTML = '<p class="text-xs text-slate-400 italic py-1">กำลังวิเคราะห์…</p>';
  const p = new URLSearchParams(); if (getBranch()) p.set('branch', getBranch()); p.set('limit', '15');
  let m; try { m = await api(`${API}/api/home/menu-movers?${p}`); }
  catch (e) { el.innerHTML = '<p class="text-xs text-red-400">โหลดไม่สำเร็จ</p>'; return; }
  const s = m && m.sales, b = s && s.buffet;
  if (!s || !b) { el.innerHTML = '<p class="text-xs text-slate-400 italic py-1">ยังไม่มีข้อมูลพอจะวิเคราะห์ (เลือกช่วง/สาขาที่มียอด)</p>'; return; }
  const pct = (t, a) => (a ? Math.round((t - a) / a * 100) : null);
  const sgn = v => (v == null ? '—' : (v >= 0 ? '+' : '') + v + '%');
  const c = v => (v == null ? 'var(--text-muted)' : (v >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'));
  const covP = pct(b.covers_today, b.covers_avg), revP = pct(b.rev_today, b.rev_avg);
  const ppcP = pct(b.ppc_today, b.ppc_avg), alaP = pct(s.alacarte_today, s.alacarte_avg);
  const up = m.up || [], down = m.down || [];
  const dishDir = up.length > down.length ? 'พุ่ง' : (down.length > up.length ? 'ตก' : 'ทรงตัว');
  const names = arr => arr.slice(0, 3).map(x => esc(x.menu)).join(', ') || '—';
  const lines = [];
  if (revP != null) lines.push(`ยอด<b>บุฟเฟต์</b> (${fmtDateTh(m.date)}) ${revP >= 0 ? 'โต' : 'ลด'} <b style="color:${textInk(c(revP))}">${sgn(revP)}</b> · โต๊ะ ${sgn(covP)} เทียบเฉลี่ย 21 วัน — เป็นรายได้หลักของร้าน`);
  if (ppcP != null) lines.push(ppcP >= 0
    ? `ราคาเฉลี่ย/หัว <b style="color:var(--good-ink)">${sgn(ppcP)}</b> → ลูกค้าเลือก<b>ชุดราคาสูงขึ้น</b> (มื้อเย็น/พรีเมียม)`
    : `ราคาเฉลี่ย/หัว <b style="color:var(--bad-ink)">${sgn(ppcP)}</b> → mix เอียงไป<b>ชุดราคาถูกลง</b> (กลางวัน/เด็ก)`);
  if (covP != null) {
    const same = (covP >= 0 && up.length >= down.length) || (covP < 0 && down.length >= up.length);
    lines.push(same
      ? `เมนูที่กินต่อหัว<b>${dishDir}</b> <b style="color:var(--good-ink)">ไปทางเดียวกัน</b>กับยอดบุฟเฟต์ — สอดคล้องกับจำนวนลูกค้า`
      : `เมนูที่กินต่อหัว<b>${dishDir}</b> <b style="color:var(--amber-ink)">สวนทาง</b>กับยอดบุฟเฟต์ — เป็นเรื่องรสนิยม/ความนิยม ไม่ใช่แค่จำนวนลูกค้า`);
  }
  if (alaP != null) lines.push(`เมนู<b>à-la-carte</b> (สั่งจ่ายเพิ่ม) ${alaP >= 0 ? 'โต' : 'หด'} <b style="color:${textInk(c(alaP))}">${sgn(alaP)}</b>`);
  lines.push(`เทรนด์เมื่อวาน — มาแรง: <b style="color:var(--good-ink)">${names(up)}</b> · แผ่วลง: <b style="color:var(--bad-ink)">${names(down)}</b>`);
  el.innerHTML = `<div class="text-xs space-y-1.5" style="color:var(--text-primary);line-height:1.5">${lines.map(t => `<div>• ${t}</div>`).join('')}</div>`;
}

// Yesterday's revenue by hour — peak + quietest open hour.
function renderHomePeakHours(hourly, date) {
  const sumEl = document.getElementById('home-peak-summary');
  if (!Array.isArray(hourly) || !hourly.length) { if (sumEl) sumEl.textContent = 'ไม่มีข้อมูลรายชั่วโมง'; return; }
  const open = hourly.filter(h => (h.tx_count || 0) > 0);
  if (!open.length) { if (sumEl) sumEl.textContent = 'ไม่มีข้อมูลรายชั่วโมง'; return; }
  const peak = open.reduce((a, b) => (b.revenue > a.revenue ? b : a));
  const quiet = open.reduce((a, b) => (b.revenue < a.revenue ? b : a));
  const lo = open[0].hour, hi = open[open.length - 1].hour;
  const span = hourly.filter(h => h.hour >= lo && h.hour <= hi);
  const hh = h => String(h).padStart(2, '0') + ':00';
  if (sumEl) sumEl.innerHTML = `พีค <b style="color:var(--text-primary)">${hh(peak.hour)}</b> (${fmt(peak.revenue)}) · เงียบสุด <b style="color:var(--text-primary)">${hh(quiet.hour)}</b> · ${date || ''}`;
  mkChart('chartHomePeak', {
    type: 'bar',
    data: {
      labels: span.map(h => hh(h.hour)),
      datasets: [{ data: span.map(h => h.revenue),
        backgroundColor: span.map(h => h.hour === peak.hour ? '#4a70b0' : 'rgba(31,58,107,0.30)'), borderRadius: 3 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => fmt(v) } }, x: { ticks: { font: { size: 9 } } } }
    }
  });
}

// Last-7-day company revenue trend + momentum read.
function renderHomeTrend7(sd) {
  const sumEl = document.getElementById('home-trend7-summary');
  const tr = (sd && sd.trend ? sd.trend : []).slice(-7);
  if (!tr.length) { if (sumEl) sumEl.textContent = 'ไม่มีข้อมูล'; return; }
  const revs = tr.map(x => x.revenue || 0);
  const last = revs[revs.length - 1];
  const avg = revs.reduce((s, v) => s + v, 0) / revs.length;
  const vsAvg = avg ? (last - avg) / avg * 100 : null;
  // momentum: mean of last 3 vs first 4
  const head = revs.slice(0, Math.max(1, revs.length - 3)), tail = revs.slice(-3);
  const hAvg = head.reduce((s, v) => s + v, 0) / head.length, tAvg = tail.reduce((s, v) => s + v, 0) / tail.length;
  const mom = hAvg ? (tAvg - hAvg) / hAvg * 100 : null;
  const momTxt = mom == null ? '' : mom >= 3 ? `<b style="color:var(--good-ink)">▲ กำลังเร่งขึ้น ${mom.toFixed(0)}%</b>` : mom <= -3 ? `<b style="color:var(--bad-ink)">▼ กำลังชะลอ ${Math.abs(mom).toFixed(0)}%</b>` : '<b>ทรงตัว</b>';
  if (sumEl) sumEl.innerHTML = `${momTxt} · วันล่าสุด ${vsAvg != null ? (vsAvg >= 0 ? '+' : '') + vsAvg.toFixed(0) + '% เทียบค่าเฉลี่ย 7 วัน' : ''}`;
  const fmtDay = s => (s || '').slice(5);
  mkChart('chartHomeTrend7', {
    type: 'bar',
    data: {
      labels: tr.map(x => fmtDay(x.start)),
      datasets: [{ data: revs,
        backgroundColor: revs.map((_, i) => i === revs.length - 1 ? '#059669' : 'rgba(5,150,105,0.30)'), borderRadius: 3 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => fmt(v) } }, x: { ticks: { font: { size: 9 } } } }
    }
  });
}

// ── Branch scorecard for Home (yesterday performance, richer than a bar) ──
// DV2: ranked by the baht below the branch's own normal (the trailing 7-day average), most below first; "ต่างจากปกติ" is column 2 with the %
// under it; the 5 lowest and the 5 highest show, the rest behind "ดูทั้งหมด"; the top bar's branch narrows the list.
async function renderHomeScorecard(focusDate) {
  const el = document.getElementById('home-branch-scorecard');
  if (!el) return;
  el.innerHTML = '<p class="text-xs text-slate-400 py-3">กำลังโหลด…</p>';
  const p = new URLSearchParams();
  if (focusDate) p.set('date', focusDate);
  if (getBranch()) p.set('branch', getBranch());
  if (getGroup()) p.set('group', getGroup());
  let d;
  try { d = await api(`${API}/api/home/branch-scorecard?${p}`); }
  catch (e) { el.innerHTML = '<p class="text-xs text-red-500 py-3">โหลดไม่สำเร็จ</p>'; return null; }
  const rows = (d.branches || []).slice().sort((a, b) => (a.vs_avg_baht ?? Infinity) - (b.vs_avg_baht ?? Infinity) || (b.revenue || 0) - (a.revenue || 0));
  if (!rows.length) { el.innerHTML = '<p class="text-xs text-slate-400 py-3">ไม่มีข้อมูล</p>'; return d; }
  const dot = s => s === 'up' ? '<span title="เหนือปกติ">●</span>'
                 : s === 'down' ? '<span title="ต่ำกว่าปกติ">●</span>'
                 : '<span title="ปกติ">●</span>';
  const pctCell = (v, hb = true) => v == null ? '<span class="text-slate-300">—</span>'
    : `<span style="color:${(v >= 0) === hb ? 'var(--good-ink)' : 'var(--bad-ink)'}">${v > 0 ? '▲' : v < 0 ? '▼' : ''} ${Math.abs(v).toFixed(0)}%</span>`;
  const vsCell = r => r.vs_avg_baht == null ? '<span class="text-slate-300">—</span>'
    : `<span data-ph-line class="font-semibold" style="color:${r.vs_avg_baht >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'}">${dvBaht(r.vs_avg_baht)}</span><span data-ph-line class="block" style="color:var(--text-muted)">${r.vs_avg_pct != null ? (r.vs_avg_pct > 0 ? '+' : '') + r.vs_avg_pct.toFixed(0) + '%' : ''} · ปกติ ${fmt(r.avg7)}</span>`;
  const n = rows.length, TOP = 5, BOT = 5;
  el.innerHTML = `<table class="text-xs w-full" id="homeScoreTable">
    <thead><tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">
      <th class="py-1.5 pr-2"></th><th class="py-1.5 pr-2">สาขา</th>
      <th class="py-1.5 pr-2 text-right">ต่างจากปกติ</th><th class="py-1.5 pr-2 text-right">ยอดเมื่อวาน</th>
      <th class="py-1.5 pr-2 text-center">7 วัน</th><th class="py-1.5 pr-2 text-right">vs สัปดาห์ก่อน</th>
      <th class="py-1.5 pr-2 text-right">ยอด/หัว</th><th class="py-1.5 text-right">บิล</th>
    </tr></thead><tbody>` +
    rows.map((r, i) => `<tr class="border-t border-slate-100 hover:bg-indigo-50/40 cursor-pointer"${dvMoreAttr('homeScore', i, n, TOP, BOT)} onclick="homeDrillBranch('${(r.branch || '').replace(/'/g, "\\'")}')" title="เจาะดูสาขา ${esc(r.branch)}">
      <td class="py-1.5 pr-2">${dot(r.status)}</td>
      <td class="py-1.5 pr-2 font-medium">${esc(r.branch)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${vsCell(r)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(r.revenue)}</td>
      <td class="py-1.5 pr-2 text-center">${_sparkSvg(r.spark, r.status === 'down' ? '#ef4444' : '#10b981')}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${pctCell(r.vs_wow_pct)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${r.rev_per_head != null ? fmt(r.rev_per_head) : '—'}</td>
      <td class="py-1.5 text-right tabular-nums">${fmtN(r.bills)}</td>
    </tr>`).join('') + `</tbody></table>` + dvMoreBtn('homeScore', n, TOP, BOT);
  // DV5 · the answer strip: the two branches furthest below their normal in baht, and the chain against its normal
  const below = rows.filter(r => r.vs_avg_baht != null && r.vs_avg_baht < 0);
  const pctS = r => r.vs_avg_pct != null ? `${ansPct(r.vs_avg_pct, 0)} · ปกติ ฿${fmt(r.avg7)}` : '';
  const sumR = rows.reduce((a, r) => a + (r.revenue || 0), 0), sumV = rows.reduce((a, r) => a + (r.vs_avg_baht || 0), 0);
  const base = sumR - sumV;
  pageAnswer('home', [
    below[0] && ansLine('ต่ำกว่าปกติมากสุด', `${below[0].branch} ${dvBaht(below[0].vs_avg_baht)}`, pctS(below[0]), 'bad', '#homeScoreTable'),
    below[1] && ansLine('อันดับถัดไป', `${below[1].branch} ${dvBaht(below[1].vs_avg_baht)}`, pctS(below[1]), 'bad', '#homeScoreTable'),
    rows.length && ansLine(getBranch() ? 'ยอดของสาขาเทียบปกติ' : 'ทั้งเครือเทียบปกติ', `฿${fmt(sumR)} · ${dvBaht(sumV)}`,
      base > 0 ? `${ansPct(sumV / base * 100)} · ปกติ = เฉลี่ย 7 วันก่อนหน้า` : '', ansTone(sumV), '#homeScoreTable'),
  ]);
  return d;
}

// ── ยอดขายรายวัน (Daily Sales) tab ──
// PR-PP2: the period is the top bar's (วัน · สัปดาห์ · เดือน · ปี on this page, TABS follow.period.kinds; the stepper moves the
// day / week / month / year). A period not finished is to date through the last closed day; every figure shows เทียบช่วงก่อน +
// เทียบปีก่อน (periods.resolve). The page's own date picker and chip track are gone: one period control per page.
// the older entry points (a deep link, a drill) move the top bar: dsSetGran('week'), dsSetDate('2026-10-14')
function dsSetGran(g) { if (['day', 'week', 'month', 'year'].includes(g)) gPeriodSet({ kind: g, pick: gPeriodPick().pick }); }
function dsSetDate(iso) { if (/^\d{4}-\d{2}-\d{2}$/.test(String(iso || ''))) gPeriodSet({ kind: 'day', pick: iso }); }
// DV5 · the answer strip of ยอดขายรายวัน: the branch that lost the most baht, the one that gained the most, and the chain's trend
// (rows = the branches ranked by the baht change, the largest loss first; chgOf = that change; t = the chain's total)
function _dsAnswer(rows, chgOf, t, sameBase) {
  const vs = sameBase ? 'ปีก่อน' : 'ช่วงก่อน', base = t ? (sameBase ? t.ly : t.prev) : null;
  const ch = rows.filter(b => chgOf(b) != null);
  const lo = ch.filter(b => chgOf(b) < 0), hi = ch.filter(b => chgOf(b) > 0).reverse();
  const next = (xs) => xs.length > 1 ? 'ถัดไป ' + xs.slice(1, 3).map(b => `${b.branch} ${dvBaht(chgOf(b))}`).join(' · ') : '';
  const pct = b => ansPct(sameBase ? b.yoy_pct : b.mom_pct);
  const out = [];
  if (ch.length > 1) {
    if (lo.length) out.push(ansLine(`ยอดลดมากสุด เทียบ${vs}`, `${lo[0].branch} ${dvBaht(chgOf(lo[0]))}`, [pct(lo[0]), next(lo)].filter(Boolean).join(' · '), 'bad', '#dsBody'));
    if (hi.length) out.push(ansLine(`ยอดเพิ่มมากสุด เทียบ${vs}`, `${hi[0].branch} ${dvBaht(chgOf(hi[0]))}`, [pct(hi[0]), next(hi)].filter(Boolean).join(' · '), 'good', '#dsBody'));
  }
  if (t && t.revenue != null) {
    const d = base && base.revenue != null ? t.revenue - base.revenue : null, p = base && base.revenue ? d / base.revenue * 100 : null;
    out.push(ansLine(getBranch() ? 'ยอดขายของสาขา' : 'ยอดขายทั้งเครือ', `฿${fmt(t.revenue)}${d != null ? ' · ' + dvBaht(d) : ''}`,
      p != null ? `${ansPct(p)} เทียบ${vs}` : '', ansTone(d), '#dsTrend'));
  }
  return out;
}
async function loadDailySales(force) {
  const G = gPeriodPick();
  const gran = ['day', 'week', 'month', 'year'].includes(G.kind) ? G.kind : 'month';
  const dv = G.pick || '';
  const _fk = _filterKey() + '|' + gran + '|' + dv;
  if (!force && _tabLastKey.dailysales === _fk) return;
  _tabLastKey.dailysales = _fk;
  const p = new URLSearchParams(); p.set('gran', gran);
  if (dv) p.set('date', dv);
  if (getBranch()) p.set('branch', getBranch());
  if (getGroup()) p.set('group', getGroup());
  let d;
  try { d = await api(`${API}/api/sales/daily?${p}`); }
  catch (e) { return; }
  const r = periodOf(d);
  periodChip(r, true);                                   // the range, once, beside the page title
  document.getElementById('dsCmpNote').innerHTML = periodQ(r);
  const t = d.total || {}, tp = t.prev || null, tl = t.ly || null;
  document.getElementById('dsKpis').innerHTML = [
    { l: 'ยอดขายรวม (ex-VAT)', v: fmt(t.revenue || 0), c: '#4a70b0', cur: t.revenue, prev: tp && tp.revenue, ly: tl && tl.revenue },
    { l: 'ลูกค้า (Pax)', v: fmtN(t.customers || 0), c: '#0891b2', cur: t.customers, prev: tp && tp.customers, ly: tl && tl.customers },
    { l: 'ยอด/หัว', v: fmt(t.rev_per_head || 0), c: '#7a66b8', cur: t.rev_per_head, prev: tp && tp.rev_per_head, ly: tl && tl.rev_per_head },
    { l: 'บิล', v: fmtN(t.bills || 0), c: '#0d9488', cur: t.bills, prev: tp && tp.bills, ly: tl && tl.bills },
  ].map((k, i) => periodKpi(Object.assign(k, { range: r, q: i === 0 }))).join('');
  const an = d.anomalies || [];
  document.getElementById('dsAnomaly').innerHTML = an.length
    ? `<div class="card p-4 notice-warn"><p class="text-xs font-semibold mb-2">ผิดปกติ (เทียบค่าปกติ)</p>` + an.slice(0, 8).map(a => `<div class="text-xs">• ${esc(a.msg)}</div>`).join('') + `</div>`
    : '';
  const thP = document.getElementById('dsThPrev'), thL = document.getElementById('dsThLy');
  const sameBase = r && r.prev[0] === r.ly[0] && r.prev[1] === r.ly[1];
  if (thP) { thP.title = r ? r.prev_label_th : ''; thP.hidden = !!sameBase; }
  if (thL) thL.title = r ? r.ly_label_th : '';
  // DV2: column 2 = the baht change against the period before (the year before when the two bases are the same); the rows rank by it,
  // the largest loss first; the 5 largest losses and the 5 largest gains show, the rest behind "ดูทั้งหมด"
  const chgOf = b => sameBase ? b.yoy_delta : b.mom_delta;
  const thC = document.getElementById('dsThChg');
  if (thC) thC.title = r ? `${r.label_th} เทียบ ${sameBase ? r.ly_label_th : r.prev_label_th}` : '';
  const dsRows = (d.branches || []).slice().sort((a, b) => (chgOf(a) ?? Infinity) - (chgOf(b) ?? Infinity) || (b.revenue || 0) - (a.revenue || 0));
  const dsN = dsRows.length;
  document.getElementById('dsMore').innerHTML = dvMoreBtn('dsBody', dsN, 5, 5);
  document.getElementById('dsBody').innerHTML = dsRows.map((b, i) => `<tr class="border-t border-slate-100 hover:bg-indigo-50/40"${dvMoreAttr('dsBody', i, dsN, 5, 5)}>
      <td class="py-1.5 pr-2 font-medium">${esc(b.branch)}</td>
      <td class="py-1.5 pr-2 text-right font-semibold tabular-nums"${chgOf(b) != null ? ` style="color:${chgOf(b) >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'}"` : ''}>${chgOf(b) != null ? dvBaht(chgOf(b)) : '—'}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(b.revenue)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(b.customers)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${b.rev_per_head != null ? fmt(b.rev_per_head) : '—'}</td>
      ${sameBase ? '' : `<td class="py-1.5 pr-2 text-right tabular-nums">${periodPctDelta(b.mom_pct, { title: r ? `${r.label_th} เทียบ ${r.prev_label_th}${b.mom_rev != null ? ' · ฿' + fmt(b.mom_rev) : ''}` : '' })}</td>`}
      <td class="py-1.5 text-right tabular-nums">${periodPctDelta(b.yoy_pct, { title: r ? `${r.label_th} เทียบ ${r.ly_label_th}${b.yoy_rev != null ? ' · ฿' + fmt(b.yoy_rev) : ''}` : '' })}</td>
    </tr>`).join('') || `<tr><td colspan="${sameBase ? 6 : 7}" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>`;
  pageAnswer('dailysales', _dsAnswer(dsRows, chgOf, t, sameBase));         // DV5: the answer strip on a phone
  const tr = d.trend || [];
  const tLab = x => gran === 'year' ? String(+x.start.slice(0, 4) + 543)
    : gran === 'month' ? `${_PR_MON[+x.start.slice(5, 7) - 1]} ${(+x.start.slice(0, 4) + 543) % 100}` : periodLabelTh(x.start, x.start).replace(/ \d{4}$/, '');
  mkChart('dsTrend', { type: 'bar',
    data: { labels: tr.map(tLab), datasets: [{ data: tr.map(x => x.revenue),
      backgroundColor: tr.map((x, i) => i === tr.length - 1 ? '#4a70b0' : 'rgba(31,58,107,0.4)'), borderRadius: 4 }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { title: items => { const x = tr[items[0].dataIndex]; return x ? periodLabelTh(x.start, x.end) + (x.partial ? ' (ถึงวันที่ปิดยอดล่าสุด)' : '') : ''; } } } },
      scales: { y: { ticks: { callback: v => fmt(v) } }, x: { ticks: { font: { size: 9 } } } } } });
  renderMenuSpike(d.focus);
}

// periodRange (the one period helper, PR-PP1) lives in js/01-core.js.

// #5 menu-spike: which menus were abnormally high PER HEAD (plates per 100 customers, PR-ST), per branch. Period chips (PR-SP, owner 2026-10-24), styled as the
// page's gran-btn track so a shared period component can replace them: วัน = the focus day vs the same weekday of the 3 weeks before ·
// สัปดาห์นี้ / เดือนนี้ = Monday / the 1st -> the focus day (to date) · กำหนดเอง = จาก–ถึง. A period is compared with
// "ปกติ" (weekday-fair, the 3 weeks before it; backend _menu_spike_period), not with the previous period: a spike is vs normal.
// Shows ALL items ≥1.5×; has its own branch picker; the chosen period is remembered on this device.
let _spikeBranchesLoaded = false, _spikeFocus = null, _spikeSeq = 0, _spikeLast = null;
// DV3 (phone table review 2026-10-25): สัปดาห์ by default - one day swings too much; a new storage key, so a choice stored before is dropped
const _SPIKE_LS = 'oshinei_spike_period_v2';
const _SPIKE_MODES = ['day', 'week', 'mtd', 'range'];
try { localStorage.removeItem('oshinei_spike_period'); } catch (e) { /* no storage */ }
let _spikeMode = (() => {
  try { const v = JSON.parse(localStorage.getItem(_SPIKE_LS) || 'null'); if (v && _SPIKE_MODES.includes(v.m)) return v; } catch (e) {}
  return { m: 'week', f: '', t: '' };
})();
function _spikeSave() { try { localStorage.setItem(_SPIKE_LS, JSON.stringify(_spikeMode)); } catch (e) {} }
// PR-TD (owner 2026-10-25) · กำหนดเอง: ช่วงนี้ = a Mon–Sun week (pk 'week') or จาก–ถึง (pk 'range'); เทียบกับ = ปกติ (bk 'normal', the 3 weeks
// before), a chosen week (bk 'week') or a chosen range (bk 'range') -> base_from / base_to (backend _spike_base: it ends before ช่วงนี้ starts)
const _SPIKE_MON = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
const _spikeD = iso => new Date(iso + 'T00:00:00Z');
const _spikeIso = d => d.toISOString().slice(0, 10);
const _spikeAdd = (iso, n) => { const d = _spikeD(iso); d.setUTCDate(d.getUTCDate() + n); return _spikeIso(d); };
// "13–19 ต.ค." · "29 ก.ย.–5 ต.ค." · a different year adds it (Buddhist era, 2 digits)
function _spikeSpan(a, b) {
  if (!a) return '';
  const x = _spikeD(a), y = _spikeD(b || a);
  const m = d => _SPIKE_MON[d.getUTCMonth()], yr = d => ` ${(d.getUTCFullYear() + 543) % 100}`;
  const ny = x.getUTCFullYear() !== new Date().getUTCFullYear() ? yr(y) : '';
  if (a === (b || a)) return `${x.getUTCDate()} ${m(x)}${ny}`;
  return x.getUTCMonth() === y.getUTCMonth() && x.getUTCFullYear() === y.getUTCFullYear()
    ? `${x.getUTCDate()}–${y.getUTCDate()} ${m(y)}${ny}` : `${x.getUTCDate()} ${m(x)}–${y.getUTCDate()} ${m(y)}${ny}`;
}
// the finished Mon–Sun weeks up to the last finished day (newest first), 16 of them
function _spikeWeeks() {
  const last = (_spikeLast && _spikeLast.latest_day) || _spikeFocus || _spikeIso(new Date());
  const d = _spikeD(last), dow = (d.getUTCDay() + 6) % 7;          // Monday = 0
  let sun = _spikeAdd(last, dow === 6 ? 0 : -(dow + 1));
  const out = [];
  for (let i = 0; i < 16; i++) { const mon = _spikeAdd(sun, -6); out.push({ f: mon, t: sun, l: `สัปดาห์ ${_spikeSpan(mon, sun)}` }); sun = _spikeAdd(sun, -7); }
  return out;
}
function _spikeFillWeeks(sel, want) {
  if (!sel) return;
  const ws = _spikeWeeks();
  if (want && !ws.some(w => w.f === want)) ws.unshift({ f: want, t: _spikeAdd(want, 6), l: `สัปดาห์ ${_spikeSpan(want, _spikeAdd(want, 6))}` });
  sel.innerHTML = ws.map(w => `<option value="${w.f}">${esc(w.l)}</option>`).join('');
  if (want && ws.some(w => w.f === want)) sel.value = want;
}
// shows the pickers of the chosen kinds; fills the week lists (ช่วงนี้ = the latest week, เทียบกับ = the week before it)
function spikeCustomUi() {
  const ck = document.getElementById('spikeCurKind'), bk = document.getElementById('spikeBaseKind');
  if (!ck || !bk) return;
  const cw = document.getElementById('spikeCurWeek'), bw = document.getElementById('spikeBaseWeek');
  if (cw && !cw.options.length) _spikeFillWeeks(cw, _spikeMode.pk === 'week' ? _spikeMode.f : '');
  if (bw && !bw.options.length) { _spikeFillWeeks(bw, _spikeMode.bk === 'week' ? _spikeMode.bf : ''); if (!(_spikeMode.bk === 'week' && _spikeMode.bf) && bw.options.length > 1) bw.selectedIndex = Math.min(bw.options.length - 1, (cw ? cw.selectedIndex : 0) + 1); }
  const show = (id, on) => { const x = document.getElementById(id); if (x) x.hidden = !on; };
  show('spikeCurWeek', ck.value === 'week'); show('spikeCurRange', ck.value === 'range');
  show('spikeBaseWeek', bk.value === 'week'); show('spikeBaseRange', bk.value === 'range');
}
function _spikeChips() {
  document.querySelectorAll('#spikeModes [data-m]').forEach(b => {
    const on = b.dataset.m === _spikeMode.m;
    b.classList.toggle('gran-active', on); b.setAttribute('aria-pressed', on ? 'true' : 'false');
    const tr = b.parentElement;                  // a phone scrolls the chip row: keep the chosen chip in view
    if (on && tr && tr.scrollWidth > tr.clientWidth) tr.scrollLeft = Math.max(0, b.offsetLeft - tr.offsetLeft - 8);
  });
  const box = document.getElementById('spikeCustom');
  if (box) box.hidden = _spikeMode.m !== 'range';
  if (_spikeMode.m === 'range') {
    const ck = document.getElementById('spikeCurKind'), bk = document.getElementById('spikeBaseKind');
    if (ck && !ck.dataset.init) { ck.value = _spikeMode.pk || 'range'; ck.dataset.init = '1'; }
    if (bk && !bk.dataset.init) { bk.value = _spikeMode.bk || 'normal'; bk.dataset.init = '1'; }
    const bf = document.getElementById('spikeBaseFrom'), bt = document.getElementById('spikeBaseTo');
    if (_spikeMode.bk === 'range') { if (bf && !bf.value) bf.value = _spikeMode.bf || ''; if (bt && !bt.value) bt.value = _spikeMode.bt || ''; }
    spikeCustomUi();
  }
}
function spikeSetMode(m) {
  if (!_SPIKE_MODES.includes(m)) return;
  _spikeMode.m = m;
  if (m === 'range') {
    const f = document.getElementById('spikeFrom'), t = document.getElementById('spikeTo');
    const end = _spikeMode.t || _spikeFocus || '';
    if (t && !t.value) t.value = end;
    if (f && !f.value && end) { const d = new Date(end + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - 6); f.value = _spikeMode.f || d.toISOString().slice(0, 10); }
  }
  _spikeSave(); _spikeChips();
  if (m !== 'range' || (_spikeMode.f && _spikeMode.t)) renderMenuSpike();
}
function spikeApplyRange() {
  const v = id => document.getElementById(id)?.value || '';
  const el = document.getElementById('dsSpike');
  const bad = msg => { if (el) el.innerHTML = `<p class="text-xs py-2" style="color:var(--bad-ink)">${msg}</p>`; };
  const pk = v('spikeCurKind') || 'range', bk = v('spikeBaseKind') || 'normal';
  let f = v('spikeFrom'), t = v('spikeTo');
  if (pk === 'week') { f = v('spikeCurWeek'); t = f ? _spikeAdd(f, 6) : ''; }
  if (!f || !t) { bad('ระบุวันที่ทั้งช่อง จาก และ ถึง'); return; }
  if (f > t) { bad('วันที่ จาก ต้องไม่อยู่หลังวันที่ ถึง'); return; }
  let bf = '', bt = '';
  if (bk === 'week') { bf = v('spikeBaseWeek'); bt = bf ? _spikeAdd(bf, 6) : ''; }
  if (bk === 'range') { bf = v('spikeBaseFrom'); bt = v('spikeBaseTo'); }
  if (bk !== 'normal') {
    if (!bf || !bt) { bad('ระบุวันที่ของช่วงที่ใช้เทียบทั้งสองช่อง'); return; }
    if (bf > bt) { bad('วันเริ่มต้นของช่วงที่ใช้เทียบต้องไม่อยู่หลังวันสิ้นสุด'); return; }
    if (bt >= f) { bad('ช่วงที่ใช้เทียบต้องสิ้นสุดก่อนวันเริ่มต้นของช่วงที่เลือก'); return; }
  }
  _spikeMode = { m: 'range', f, t, pk, bk, bf, bt }; _spikeSave(); _spikeChips(); renderMenuSpike();
}
// PR-ST (owner 2026-10-25) · per head: rate = plates per 100 customers (buffet menus on buffet customers, the rest on all
// customers; backend _SPIKE_PER), "เพิ่มจาก tier" = the buffet tiers (and ไม่ใช่บุฟเฟ่ต์) whose plates rose above their own normal.
// PR-TD (owner 2026-10-25): one tier per line in the cell (label left, +จาน right), up to 4 lines, then "+N tier"
const _SPIKE_TIER_SHOW = 4;
const _SPIKE_BASIS = { buffet: 'ลูกค้าบุฟเฟ่ต์', all: 'ลูกค้าทั้งหมด', days: 'ไม่มีจำนวนลูกค้า (เทียบจานต่อวัน)' };
// PR-SC (owner 2026-10-25) · ต้นทุนเพิ่ม: for a role that sees cost (d.cost_view) the rows come sorted by the food cost of the extra plates
// (extra plates at the normal per-head rate x the dish cost of that month); every role sees the first d.top (15) rows, "ดูทั้งหมด (N)" opens the
// rest, remembered on this device. A role without cost gets no cost key from the server: no cost column, the order by +จาน as before.
const _SPIKE_ALL_LS = 'oshinei_spike_all';
let _spikeAll = (() => { try { return localStorage.getItem(_SPIKE_ALL_LS) === '1'; } catch (e) { return false; } })();
let _spikeOneBr = '';
function spikeToggleAll() {
  _spikeAll = !_spikeAll;
  try { localStorage.setItem(_SPIKE_ALL_LS, _spikeAll ? '1' : '0'); } catch (e) {}
  if (_spikeLast) _spikeDraw(_spikeLast, _spikeOneBr);
}
// DV3 (phone table review 2026-10-25) · เมนูใหม่ (per dish) is FOLDED by default; once opened this device remembers it. Its own "ดูทั้งหมด".
// The raw-plates list (แบบเดิม) left the card: it is in the CSV only.
const _SPIKE_NEW_LS = 'oshinei_spike_new_open';
try { localStorage.removeItem('oshinei_spike_plates_open_v2'); } catch (e) { /* no storage */ }
let _spikeNewOpen = (() => { try { return localStorage.getItem(_SPIKE_NEW_LS) === '1'; } catch (e) { return false; } })();
let _spikeNewAll = false;
function spikeNewFold() {
  _spikeNewOpen = !_spikeNewOpen;
  try { localStorage.setItem(_SPIKE_NEW_LS, _spikeNewOpen ? '1' : '0'); } catch (e) {}
  const b = document.getElementById('spikeNewBody'), f = document.getElementById('spikeNewFold');
  if (b) b.hidden = !_spikeNewOpen;
  if (f) { f.setAttribute('aria-expanded', _spikeNewOpen ? 'true' : 'false'); f.textContent = _spikeNewOpen ? 'ซ่อน' : 'แสดง'; }
  if (typeof phCardsSchedule === 'function') phCardsSchedule();
}
function spikeNewToggleAll() { _spikeNewAll = !_spikeNewAll; if (_spikeLast) _spikeDraw(_spikeLast, _spikeOneBr); }
const _spikeBaht = v => v == null ? '—' : '฿' + fmtN(v);
const _spikePct = (v, dp) => v == null ? '—' : Number(v).toLocaleString('th-TH', { minimumFractionDigits: dp, maximumFractionDigits: dp });
function _spikeCostNote(s) {
  if (s.package) return 'แพ็กเกจบุฟเฟ่ต์ ไม่คิดต้นทุน';
  if (s.uncosted || !(s.unit_cost > 0)) return 'ยังไม่มีต้นทุน';
  return '';
}
function _spikeTierChips(s) {
  // the tiers that add plates, largest first, up to 3 + "…"; tiers null = the fact tables do not cover the window
  if (!s.tiers) return [];
  return s.tiers.filter(t => Math.round(t.excess) >= 1);
}
function _spikeTierText(s) {
  const pos = _spikeTierChips(s);
  return pos.map(t => `${t.label} +${Math.round(t.excess)}`).join(' · ');
}
// PR-SX (owner 2026-10-25) · ขายน้อยช่วงก่อน / ขายน้อยในช่วงเทียบ: a small muted marker after the × figure instead of a grey line; the full text is
// its title and aria-label, explained once in the card's "?". The hidden [data-ph-line] line gives the phone's row sheet the full text.
const _SPIKE_MK = '◔';
function _spikeThinTxt(vsBase) { return vsBase ? 'ขายน้อยในช่วงเทียบ' : 'ขายน้อยช่วงก่อน'; }
function _spikeThinMk(vsBase) { const t = _spikeThinTxt(vsBase); return ` <span class="sp-mk" role="img" aria-label="${t}" title="${t}">${_SPIKE_MK}</span>`; }
function _spikeThinLine(vsBase) { return `<div class="sp-mk-full" data-ph-line="" hidden>${_SPIKE_MK} ${_spikeThinTxt(vsBase)}</div>`; }
// PR-SX · widths of a table (%, one per column) as a colgroup; the fixed layout applies on a wide desktop only (CSS .sp-fixed)
function _spikeCols(ws) { return `<colgroup>${ws.map(w => `<col style="width:${w}%">`).join('')}</colgroup>`; }
// PR-TD · a menu that came back after >= 21 days without a sale at the branch (backend _spike_returns): the chip and its tooltip
function _spikeBackChip(m) {
  if (!m || !m.returning) return '';
  const tip = `ไม่มียอดขายที่สาขานี้อย่างน้อย 21 วันก่อนวันที่ ${fmtDateTh(m.returned_on)}`
    + (m.first_sold_ever ? ` · ขายครั้งแรก ${fmtDateTh(m.first_sold_ever)}` : '') + ' · นับเป็นเมนูใหม่ 28 วันนับจากวันที่กลับมาขาย';
  return ` <span class="sp-back-chip" title="${esc(tip)}">กลับมาขาย</span>`;
}
// DV3 (2026-10-25): this section is no longer drawn on the menu-spike card (spikeHvLoad / spikeHvHtml are not called from it); DV4 moves the
// view into High Value Product Monitor. /api/sales/ingredient-groups-plates stays as it is.
// ── owner 2026-10-25 · วัตถุดิบมูลค่าสูง · จำนวนจาน ("เอาไปโชว์ในเมนูยอดขาย ต่อท้าย เมนูใหม่ แต่โชว์เป็นจำนวนจานที่ออก ไม่ต้องจำนวนเงิน และให้
// เลือกช่วงเวลาได้"): the 8 groups of the High Value Product Monitor (backend/ingredient_groups.py, one definition) in PLATES only, after the
// เมนูใหม่ section, on the card's own period (its chips, กำหนดเอง and เทียบกับ: base_from / base_to) and branch select
// (/api/sales/ingredient-groups-plates - no cost key, the card's gate: every role that sees the card sees it). One group = the table
// สาขา · จานที่ออก · จาน/หัว · ลูกค้า · สาเหตุที่เปลี่ยน (the change in plates split ลูกค้า · กินต่อหัว, LMDI); ทั้งหมด = branches x groups.
// Owner 2026-10-25 (390 px, week mode): every figure shows its base under it ("▲0.9% · เดิม 193,477"); the section's own week pair
// ("w1 vs w7", Monday–Sunday weeks, ISO numbers W1…W53) beside the card's period; the 9 chips wrap on a phone (no sideways scroll); bigger figures.
const _SPHV_LS = 'oshinei_sphv_slug', _SPHV_WLS = 'oshinei_sphv_weeks';
const _SPHV_CAUSE = { customers: 'ลูกค้า', plates_per_head: 'กินต่อหัว' };
const _SPHV_SHORT = { customers: 'ลูกค้า', plates_per_head: 'กิน' };
const _SPHV_WD = ['จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.', 'อา.'];
// the 8 groups of backend/ingredient_groups.py GROUPS, until the first answer names them
const _SPHV_GROUPS = [['salmon', 'แซลมอน'], ['tuna', 'ทูน่า'], ['hamachi', 'ฮามาจิ'], ['amaebi', 'กุ้งหวาน'], ['scallop', 'หอยเชลล์'], ['eel', 'ปลาไหล'], ['oyster', 'หอยนางรม'], ['beef', 'เนื้อ']].map(([slug, label]) => ({ slug, label }));
let _spHv = { slug: 'salmon', d: null, key: '', seq: 0, err: '', p: null, weeks: [], latest: '', groups: null };
// the week pair: a = the Monday of the chosen week, b = the Monday of the week it is compared with; a = '' -> ตามช่วงของการ์ด
let _spHvW = { a: '', b: '' };
try { const s = localStorage.getItem(_SPHV_LS); if (s) _spHv.slug = s; } catch (e) { /* no storage */ }
try {
  const w = JSON.parse(localStorage.getItem(_SPHV_WLS) || 'null'), ok = x => typeof x === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(x);
  if (w && ok(w.a) && ok(w.b) && w.a !== w.b) _spHvW = { a: w.a, b: w.b };
} catch (e) { /* no storage */ }
const _spHvH = v => v == null || !isFinite(Number(v)) ? '—' : Number(v).toLocaleString('th-TH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const _spHvS = v => v == null || !isFinite(Number(v)) ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : '±'}${fmtN(Math.abs(v))} จาน`;
// one change under a figure, then the base value ("▲0.9% · เดิม 193,477"; เทียบ when the base week is the later one):
// up = 'good' (customers) | 'bad' (plates per head of a costly ingredient) | '' (plates: neither). Two parts = two children (the phone sheet's k / v).
function _spHvDelta(v, up, title, was, word) {
  const t = title ? ` title="${esc(title)}"` : '';
  const b = was == null ? '' : `<span class="sp-hv-b"> · ${word || 'เดิม'} ${was}</span>`;
  if (v == null || !isFinite(Number(v))) return `<div class="sp-hv-d" data-ph-line=""${t}><span style="color:var(--text-muted)">—</span>${b}</div>`;
  v = Number(v);
  const ink = v === 0 || !up ? 'var(--text-secondary)' : (v > 0) === (up === 'good') ? 'var(--good-ink)' : 'var(--bad-ink)';
  return `<div class="sp-hv-d" data-ph-line=""${t}><span style="color:${ink}">${v > 0 ? '▲' : v < 0 ? '▼' : ''}${Math.abs(v).toFixed(1)}%</span>${b}</div>`;
}
// ISO-style week number of a day (W1 = the week with the year's first Thursday; Monday–Sunday) - the backend's periods.iso_weeks
function _spHvIsoWeek(iso) {
  const d = _spikeD(iso), day = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - day + 3);
  const y = d.getUTCFullYear(), j4 = new Date(Date.UTC(y, 0, 4));
  return { y, w: 1 + Math.round(((d.getTime() - j4.getTime()) / 864e5 - 3 + (j4.getUTCDay() + 6) % 7) / 7) };
}
function _spHvLatest() { return _spHv.latest || (_spikeLast && _spikeLast.latest_day) || _spikeFocus || ''; }
// the pair's windows: the closed days of the chosen week; a week still running compares the SAME weekdays of the other one (to date)
function _spHvPair() {
  const { a, b } = _spHvW, la = _spHvLatest();
  if (!a || !b || a === b || !la) return null;
  const days = m => Math.min(7, Math.round((_spikeD(la).getTime() - _spikeD(m).getTime()) / 864e5) + 1);
  const n = Math.min(days(a), days(b));
  if (n < 1) return null;
  return { f: a, t: _spikeAdd(a, n - 1), bf: b, bt: _spikeAdd(b, n - 1), n, toDate: n < 7, wa: _spHvIsoWeek(a).w, wb: _spHvIsoWeek(b).w, after: b > a };
}
// "W43 (จ.–ศ.) เทียบ W42 (จ.–ศ.)" (to date) · "W43 (19–25 ต.ค.) เทียบ W37 (7–13 ก.ย.)"
function _spHvPairLabel(pr) {
  const tag = (w, s, e) => `W${w} (${pr.toDate ? (pr.n > 1 ? `จ.–${_SPHV_WD[pr.n - 1]}` : 'จ.') : _spikeSpan(s, e)})`;
  return `${tag(pr.wa, pr.f, pr.t)} เทียบ ${tag(pr.wb, pr.bf, pr.bt)}`;
}
// the card's query (renderMenuSpike) -> this section's; loads once per period / branch / base. A week pair replaces the card's period.
function spikeHvLoad(p) {
  if (p) _spHv.p = p;
  p = _spHv.p; if (!p) return;
  const q = new URLSearchParams(), pr = _spHvPair();
  if (pr) {
    q.set('period', 'range'); q.set('date_from', pr.f); q.set('date_to', pr.t); q.set('base_from', pr.bf); q.set('base_to', pr.bt);
    ['branch', 'group'].forEach(k => { const v = p.get(k); if (v) q.set(k, v); });
  } else {
    q.set('period', p.get('period') || 'day');
    ['date', 'date_from', 'date_to', 'base_from', 'base_to', 'branch', 'group'].forEach(k => { const v = p.get(k); if (v) q.set(k, v); });
  }
  const key = q.toString();
  if (key === _spHv.key && (_spHv.d || _spHv.err)) { spikeHvRender(); return; }
  _spHv.key = key; _spHv.d = null; _spHv.err = '';
  const seq = ++_spHv.seq;
  spikeHvRender();
  api(`${API}/api/sales/ingredient-groups-plates?${q}`).then(d => {
    if (seq !== _spHv.seq) return;
    if (d && Array.isArray(d.weeks) && d.weeks.length) _spHv.weeks = d.weeks;
    const first = !_spHv.latest;
    if (d && d.latest_day) _spHv.latest = d.latest_day;
    // the first answer names the last closed day: a pair guessed from the page's focus day is asked again on the right days
    const np = first && _spHvW.a ? _spHvPair() : null;
    if (np && (!pr || np.t !== pr.t || np.bt !== pr.bt)) { spikeHvLoad(); return; }
    if (!d || typeof d.detail === 'string') _spHv.err = (d && d.detail) || 'โหลดไม่สำเร็จ'; else _spHv.d = d;
    spikeHvRender();
  }).catch(() => { if (seq === _spHv.seq) { _spHv.err = 'โหลดไม่สำเร็จ'; spikeHvRender(); } });
}
// the week before the chosen one (else the one after) as its first base
function _spHvDefB(a) {
  const ws = _spHv.weeks || [], i = ws.findIndex(w => w.from === a);
  if (i >= 0 && ws[i + 1]) return ws[i + 1].from;
  if (i > 0) return ws[i - 1].from;
  return _spikeAdd(a, -7);
}
function spikeHvWeek(which, v) {
  if (which === 'a') _spHvW = v ? { a: v, b: _spHvW.b && _spHvW.b !== v ? _spHvW.b : _spHvDefB(v) } : { a: '', b: '' };
  else if (v && v !== _spHvW.a) _spHvW.b = v;
  try { localStorage.setItem(_SPHV_WLS, JSON.stringify(_spHvW)); } catch (e) { /* no storage */ }
  spikeHvLoad();
}
// สัปดาห์ [W43 · 19–25 ต.ค. ▾] เทียบ [W42 · 12–18 ต.ค. ▾]; the first option ตามช่วงของการ์ด keeps the card's period and compare
function _spHvWeekCtl() {
  const ws = (_spHv.weeks || []).slice(), { a, b } = _spHvW;
  [a, b].forEach(m => { if (m && !ws.some(w => w.from === m)) ws.push({ from: m, label: `W${_spHvIsoWeek(m).w} · ${_spikeSpan(m, _spikeAdd(m, 6))}` }); });
  ws.sort((x, y) => y.from.localeCompare(x.from));
  const opt = (w, sel, dis) => `<option value="${esc(w.from)}"${sel ? ' selected' : ''}${dis ? ' disabled' : ''}>${esc(w.label)}</option>`;
  const sa = `<select id="spikeHvWa" class="sp-hv-sel" aria-label="สัปดาห์" onchange="spikeHvWeek('a', this.value)"><option value=""${a ? '' : ' selected'}>ตามช่วงของการ์ด</option>`
    + ws.map(w => opt(w, w.from === a, false)).join('') + '</select>';
  const sb = a ? `<label class="sp-hv-wg"><span class="sp-hv-wl">เทียบ</span><select id="spikeHvWb" class="sp-hv-sel" aria-label="เทียบกับสัปดาห์" onchange="spikeHvWeek('b', this.value)">`
    + ws.map(w => opt(w, w.from === b, w.from === a)).join('') + '</select></label>' : '';
  return `<div class="sp-hv-wk ph-tz-unit" id="spikeHvWeeks"><label class="sp-hv-wg"><span class="sp-hv-wl">สัปดาห์</span>${sa}</label>${sb}</div>`;
}
function spikeHvSet(slug) {
  _spHv.slug = slug;
  try { localStorage.setItem(_SPHV_LS, slug); } catch (e) { /* no storage */ }
  spikeHvRender();
}
function spikeHvRender() {
  const sec = document.getElementById('spikeHvSec'); if (!sec) return;
  sec.innerHTML = spikeHvHtml();
  const tr = document.getElementById('spikeHvChips'), sc = tr && tr.parentElement, on = tr && tr.querySelector('.gran-active');
  if (sc && on && sc.scrollWidth > sc.clientWidth + 1) sc.scrollLeft = Math.max(0, /** @type {HTMLElement} */ (on).offsetLeft - tr.offsetLeft - 8);
  if (typeof phCardsSchedule === 'function') phCardsSchedule();
}
function _spHvRows(d, slug) {
  const br = (d.branches || []).slice();
  if (slug && slug !== 'all') br.sort((a, b) => ((b.cells[slug] || {}).plates_per_head || 0) - ((a.cells[slug] || {}).plates_per_head || 0));
  // one branch in scope: its row only (the chain would repeat it)
  return br.length === 1 ? br : [Object.assign({ branch: 'ทั้งเครือ', _chain: true }, d.chain)].concat(br);
}
function _spHvCause(c) {
  const k = c && c.cause;
  if (!k || k.total == null) return `<span style="color:var(--text-muted)">${esc((k && k.reason) || '—')}</span>`;
  const main = k.main ? _SPHV_CAUSE[k.main] : '—';
  const part = key => k[key] == null ? '' : `<div class="sp-hv-part" data-ph-line="">${_SPHV_CAUSE[key]} ${_spHvS(k[key])}</div>`;
  return `<div class="sp-hv-main" data-ph-line="">สาเหตุหลัก: ${main}</div>`
    + `<div class="sp-hv-tot" data-ph-line="">${_spHvS(k.total)}${k.total_pct != null ? ` (${k.total_pct > 0 ? '+' : k.total_pct < 0 ? '−' : ''}${Math.abs(k.total_pct).toFixed(1)}%)` : ''}</div>`
    + part('customers') + part('plates_per_head');
}
function spikeHvHtml() {
  const d = _spHv.d, groups = (d && d.groups) || [];
  const slug = _spHv.slug === 'all' || groups.some(g => g.slug === _spHv.slug) ? _spHv.slug : 'salmon';
  const per = d && d.period, base = d && d.base, pr = _spHvPair();
  const full = per && base ? `${per.label_th} เทียบ ${base.label_th}` : '';
  const vs = pr ? _spHvPairLabel(pr) : full;
  const why = per && per.reasons && per.reasons.prev ? ` · ${per.reasons.prev}` : '';
  const head = `<h3 class="sp-h">วัตถุดิบมูลค่าสูง · จำนวนจาน
      <button type="button" class="ui-q" aria-label="คำอธิบาย" aria-haspopup="dialog" data-help="hlp-dailysales-hv" title="คำอธิบาย">?</button>
      ${vs ? `<span class="sp-n" id="spikeHvVs"${pr && full ? ` title="${esc(full)}"` : ''}>(${esc(vs + why)})</span>` : ''}</h3>`;
  if (groups.length) _spHv.groups = groups;      // an error or a load keeps the chips of the last answer
  const opts = [['all', 'ทั้งหมด']].concat((groups.length ? groups : (_spHv.groups || _SPHV_GROUPS)).map(g => [g.slug, g.label]));
  const chips = `<div class="sp-hv-ctl ph-tz-unit">${_spHvWeekCtl()}<div class="sp-hv-sc"><div id="spikeHvChips" class="sp-hv-chips" role="group" aria-label="วัตถุดิบ">`
    + opts.map(([k, l]) => `<button type="button" class="gran-btn${slug === k ? ' gran-active' : ''}" data-g="${esc(k)}" aria-pressed="${slug === k}" onclick="spikeHvSet('${esc(k)}')">${esc(l)}</button>`).join('')
    + '</div></div></div>';
  if (_spHv.err) return head + chips + `<p class="sp-hv-empty" style="color:var(--bad-ink)">${esc(_spHv.err)}</p>`;
  if (!d) return head + chips + '<p class="sp-hv-empty">กำลังโหลด…</p>';
  if (!d.chain || !(d.branches || []).length) return head + chips + `<p class="sp-hv-empty">${esc((d.reasons || {}).cur || 'ไม่มีข้อมูลในช่วงที่เลือก')}</p>`;
  const rows = _spHvRows(d, slug);
  const bvs = `เทียบ ${base.label_th}`;
  // the base is the later week (W vs W in any order): its value reads "เทียบ", not "เดิม"
  const bw = base && per && per.cur && base.from > per.cur[1] ? 'เทียบ' : 'เดิม';
  const nameTd = r => `<td class="sp-hv-name">${esc(r.branch)}</td>`;
  const custTd = r => `<td class="sp-num" title="${esc(bvs)}"><div class="sp-v" data-ph-line="">${fmtN(r.customers)}</div>${_spHvDelta((r.change || {}).customers, 'good', bvs, r.prev_customers == null ? null : fmtN(r.prev_customers), bw)}</td>`;
  const cls = r => r._chain ? ' class="sp-hv-chain"' : '';
  if (slug === 'all') {
    const word = c => c && c.cause && c.cause.main ? `<span class="sp-hv-cw" data-ph-line="">${_SPHV_SHORT[c.cause.main]}</span>` : '';
    return head + chips + `<div class="sp-hv-wrap" style="overflow-x:auto"><table class="sp-tbl sp-hv-tbl sp-hv-mx w-full" id="spikeHvMatrix"><thead><tr style="color:var(--text-muted)">
      <th>สาขา</th>${groups.map(g => `<th>${esc(g.label)} <span class="sp-unit">(จาน/หัว)</span></th>`).join('')}<th>ลูกค้า</th></tr></thead><tbody>`
      + rows.map(r => `<tr${cls(r)}>${nameTd(r)}${groups.map(g => { const c = r.cells[g.slug] || {};
        return `<td class="sp-num" title="${esc(`${g.label} · ${fmtN(c.plates)} จาน · ${bvs}`)}"><div class="sp-v" data-ph-line="">${_spHvH(c.plates_per_head)}</div>${_spHvDelta((c.change || {}).plates_per_head, 'bad', bvs, c.prev ? _spHvH(c.prev.plates_per_head) : null, bw)}${word(c)}</td>`; }).join('')}${custTd(r)}</tr>`).join('')
      + '</tbody></table></div>';
  }
  return head + chips + `<div class="sp-hv-wrap" style="overflow-x:auto"><table class="sp-tbl sp-hv-tbl w-full" id="spikeHvTable"><thead><tr style="color:var(--text-muted)">
    <th>สาขา</th><th class="text-right">จานที่ออก</th><th class="text-right">จาน/หัว</th><th class="text-right">ลูกค้า</th><th>สาเหตุที่เปลี่ยน</th></tr></thead><tbody>`
    + rows.map(r => { const c = r.cells[slug] || {}; const p = c.prev || {};
      return `<tr${cls(r)}>${nameTd(r)}
        <td class="sp-num" title="${esc(bvs)}"><div class="sp-v" data-ph-line="">${fmtN(c.plates)}</div>${_spHvDelta((c.change || {}).plates, '', bvs, c.prev ? fmtN(p.plates) : null, bw)}</td>
        <td class="sp-num" title="${esc(bvs)}"><div class="sp-v" data-ph-line="">${_spHvH(c.plates_per_head)}</div>${_spHvDelta((c.change || {}).plates_per_head, 'bad', bvs, c.prev ? _spHvH(p.plates_per_head) : null, bw)}</td>
        ${custTd(r)}<td class="sp-hv-cause">${_spHvCause(c)}</td></tr>`; }).join('')
    + '</tbody></table></div>';
}
// the CSV block of this section (after the card's rows, under its own header line)
function _spHvCsv() {
  const d = _spHv.d;
  if (!d || !d.chain || !(d.groups || []).length) return [];
  const head = ['ส่วน', 'วัตถุดิบ', 'สาขา', 'จานที่ออก', 'จานที่ออก ช่วงเทียบ', 'จานที่ออก เปลี่ยน (%)', 'จาน/หัว', 'จาน/หัว ช่วงเทียบ', 'จาน/หัว เปลี่ยน (%)',
    'ลูกค้า', 'ลูกค้า ช่วงเทียบ', 'ลูกค้า เปลี่ยน (%)', 'จานที่เปลี่ยน', 'จากลูกค้า (จาน)', 'จากกินต่อหัว (จาน)', 'สาเหตุหลัก', 'ช่วงนี้', 'ช่วงเทียบ'];
  const out = [head];
  _spHvRows(d, '').forEach(r => d.groups.forEach(g => {
    const c = r.cells[g.slug] || {}, p = c.prev || {}, k = c.cause || {}, ch = c.change || {};
    out.push(['วัตถุดิบมูลค่าสูง · จำนวนจาน', g.label, r.branch, c.plates, c.prev ? p.plates : '', ch.plates, c.plates_per_head, c.prev ? p.plates_per_head : '',
      ch.plates_per_head, r.customers, r.prev_customers, (r.change || {}).customers, k.total, k.customers, k.plates_per_head, k.main ? _SPHV_CAUSE[k.main] : '',
      d.period ? d.period.label_th : '', d.base ? d.base.label_th : '']);
  }));
  return out;
}
// PR-RK · the chip threshold of the answer (1.5 -> "1.5")
function _spikeThr(d) { const t = d && d.threshold != null ? Number(d.threshold) : 1.5; return String(Math.round(t * 100) / 100); }
// DV3 · the floor of the dish list, in words ("≥ ฿1,000 หรือ ≥ 0.1% ของต้นทุนอาหาร" · "≥ 20 จาน")
function _spikeFloorTxt(d) {
  const f = (d && d.dish_floor) || {};
  return f.baht != null ? `≥ ฿${fmtN(f.baht)} หรือ ≥ ${f.share_pct}% ของต้นทุนอาหาร` : `≥ ${fmtN(f.plates != null ? f.plates : 20)} จาน`;
}
function _spikeNewFloorTxt(d) {
  const f = (d && d.new_dish_floor) || {};
  return `≥ ${fmtN(f.per_day != null ? f.per_day : 20)} จาน/วัน` + (f.baht != null ? ` หรือ ≥ ฿${fmtN(f.baht)}` : '');
}
// DV3 · สาขาที่ดัน (chain view): the top 3 branches with their ฿ (plates without cost), "+N" more
function _spikePushTxt(x, cv) {
  if (!x.pushers || !x.pushers.length) return '';
  const byC = cv && !x.uncosted;
  const parts = x.pushers.map(p => `${p.branch} ${byC && p.extra_cost != null ? '฿' + fmt(p.extra_cost) : '+' + fmtN(p.excess) + ' จาน'}`);
  if (x.pushers_more) parts.push(`อีก ${fmtN(x.pushers_more)} สาขา`);
  return parts.join(' · ');
}
// DV3 · ขาย N สาขา (chain view of a new dish): the top 3 branches by plates, "+N" more
function _spikeSoldTxt(x) {
  const parts = (x.top || []).map(b => `${b.branch} ${fmtN(b.qty)}`);
  if (x.more) parts.push(`อีก ${fmtN(x.more)} สาขา`);
  return parts.join(' · ');
}
function spikeExport() {
  const d = _spikeLast;
  const sp = (d && d.spikes) || [], nm = (d && d.new_menus) || [], ds = (d && d.dishes) || [], nd = (d && d.new_dishes) || [];
  if (!d || (!sp.length && !nm.length && !ds.length && !nd.length && !(d.spikes_plates || []).length)) { uiNotice('ไม่มีรายการให้ส่งออก'); return; }
  const per = !!d.period && d.period !== 'day';
  const q = v => `"${String(v == null ? '' : v).replace(/"/g, '""')}"`;
  const cv = !!d.cost_view;
  const vb = !!(d.base && d.base.kind === 'range'), nb = vb ? `ช่วงเทียบ ${d.base.from} ถึง ${d.base.to}` : 'ปกติ';
  // DV3 · block 1 = the card's answer, one row per dish (every branch summed in the chain view): เมนูเดิม by net extra cost, then เมนูใหม่
  const dHead = ['ส่วน', 'เมนู', 'สาขาที่ขาย', per ? 'ช่วงนี้ (จาน)' : 'วันนี้ (จาน)', `${nb} (จาน)`, '+จาน สุทธิ', 'จาน/วัน', 'สูงกว่า (เท่า)', 'แนวโน้ม (%)',
    'กลับมาขาย (สาขา)', d.view === 'chain' ? 'สาขาที่ดัน / สาขาที่ขาย' : 'สาขา', 'ถึงเกณฑ์', 'สะสม (%)']
    .concat(cv ? ['ต้นทุนเพิ่มสุทธิ (บาท)', '% ของต้นทุนอาหาร', 'ต้นทุน ช่วงนี้ (บาท)'] : []);
  const brName = d.view === 'chain' ? '' : (_spikeOneBr || '');
  const dRows = ds.map(x => [cv ? 'เมนูเดิม · ดันต้นทุนเพิ่มสุทธิ' : 'เมนูเดิม · จานเพิ่มสุทธิ', x.menu, x.branches, x.today, x.expected, x.excess, '', x.x != null ? x.x : '', '', '',
    d.view === 'chain' ? _spikePushTxt(x, cv) : brName, x.material ? 'ใช่' : '', x.cum_pct != null ? x.cum_pct : '']
    .concat(cv ? [x.uncosted ? '' : x.net_cost, x.uncosted ? '' : x.cost_share, ''] : []))
    .concat(nd.map(x => ['เมนูใหม่', x.menu, x.branches, x.qty, '', '', x.per_day, '', x.trend_pct != null ? x.trend_pct : '', x.returning || '',
      d.view === 'chain' ? _spikeSoldTxt(x) : brName, x.material ? 'ใช่' : '', '']
      .concat(cv ? ['', '', x.uncosted ? '' : x.period_cost] : [])));
  // block 2 = the rows per branch x menu (as before DV3), the raw-plates list (แบบเดิม) included: the file keeps every detail
  const head = ['ส่วน', 'เมนู', 'สาขา', 'เพิ่มจาก tier', per ? 'ช่วงนี้ (จาน)' : 'วันนี้ (จาน)', 'ต่อวัน', 'ลูกค้า', 'ฐานลูกค้า', 'จาน/100 ลูกค้า',
    `${nb} (จาน/100 ลูกค้า)`, `${nb} (จาน)`, '+จาน', 'สูงกว่า (เท่า)', 'เริ่มขาย', 'ขายมาแล้ว (วัน)', 'แนวโน้ม (%)', 'กลับมาขาย',
    'ขายครั้งแรก', 'หมายเหตุ']
    .concat(cv ? ['ต้นทุนต่อจาน (บาท)', 'ต้นทุนเพิ่ม (บาท)', '% ของต้นทุนอาหาร', 'จุด (ต้นทุนอาหาร %)', 'ต้นทุน ช่วงนี้ (บาท)'] : []);
  const oldSec = cv ? 'รายสาขา · เมนูเดิม · ต้นทุนเพิ่ม' : 'รายสาขา · เมนูเดิม · จานเพิ่ม', thrTxt = _spikeThr(d);
  const rows = sp.map(s => [oldSec, s.menu, s.branch, s.tiers ? _spikeTierText(s) : (s.tier_note || ''), s.today, s.per_day, s.customers,
    _SPIKE_BASIS[s.basis] || '', s.rate, s.normal_rate, s.expected, s.excess, s.x != null ? s.x : '', '', '', '', '', '',
    [s.spike ? `พุ่ง ≥${thrTxt}×` : '', s.thin_base ? (vb ? 'ขายน้อยในช่วงเทียบ' : 'ขายน้อยช่วงก่อน') : '', s.tier_to ? `แยก tier ถึง ${fmtDateTh(s.tier_to)}` : '', cv ? _spikeCostNote(s) : ''].filter(Boolean).join(' · ')]
    .concat(cv ? [s.unit_cost, s.extra_cost, s.cost_share, s.extra_cost_per_head_pp, ''] : []))
    .concat(nm.map(m => ['รายสาขา · เมนูใหม่', m.menu, m.branch, '', m.qty, m.per_day, m.customers, _SPIKE_BASIS[m.basis] || '', m.rate, '', '', '', '',
      m.first_sold, m.age_days, m.trend && m.trend.pct != null ? m.trend.pct : '', m.returning ? 'ใช่' : '', m.returning ? (m.first_sold_ever || '') : '',
      cv ? _spikeCostNote(m) : '']
      .concat(cv ? [m.unit_cost, '', '', '', m.period_cost] : [])))
    .concat(((d && d.spikes_plates) || []).map(s => ['เมนูพุ่ง · นับจำนวนจาน (แบบเดิม)', s.menu, s.branch, '', s.today, s.per_day, '', '', '', '', s.expected,
      s.excess, s.x != null ? s.x : '', '', '', '', '', '', [s.thin_base ? (vb ? 'ขายน้อยในช่วงเทียบ' : 'ขายน้อยช่วงก่อน') : '', s.in_per_head ? `อยู่ในส่วนต่อหัวด้วย (พุ่ง ≥${thrTxt}×)` : ''].filter(Boolean).join(' · ')]
      .concat(cv ? ['', '', '', '', ''] : [])));
  const span = (per ? `${d.date_from}_${d.date_to}` : (d.date || '')) + (vb ? `_vs_${d.base.from}_${d.base.to}` : '');
  const all = [dHead].concat(dRows).concat([[]], [head], rows);
  downloadCSV(all.map(r => r.map(q).join(',')).join('\n'), `menu_spike_${per ? d.period : 'day'}_${span}.csv`);
}
// PR-TD (owner 2026-10-25) · the strip under the controls: four small tiles (2-up on a phone). DV3: ลูกค้า ช่วงนี้ · เมนูที่ถึงเกณฑ์ (เมนูเดิม,
// เมนูใหม่) · ต้นทุนเพิ่มสุทธิ = every dish above its normal minus every dish below it (cost roles; จานเพิ่มสุทธิ for the others) · the tier
// that adds the most plates.
function _spikeStrip(d, vsBase) {
  const st = document.getElementById('spikeStrip'); if (!st) return;
  const sm = d.summary || {}, cv = !!d.cost_view, dt = d.dish_totals || {};
  const pct = sm.customers_vs_base_pct;
  const pctTxt = pct == null ? '' : `${vsBase ? 'เทียบช่วงเทียบ' : 'เทียบปกติ'} ${pct > 0 ? '+' : pct < 0 ? '−' : '±'}${_spikePct(Math.abs(pct), 1)}% ต่อวัน`;
  const tile = (id, label, value, sub, opts = {}) => `<div class="sp-tile${opts.cls ? ' ' + opts.cls : ''}"${id ? ` id="${id}"` : ''}${opts.title ? ` title="${esc(opts.title)}"` : ''}>`
    + `<div class="sp-tile-l">${label}${opts.q || ''}</div><div class="sp-tile-v"${opts.ink ? ` style="color:${opts.ink}"` : ''}>${value}</div>${sub ? `<div class="sp-tile-s">${sub}</div>` : ''}</div>`;
  const sg = v => (v > 0 ? '+' : v < 0 ? '−' : '±');
  const ink = v => v > 0 ? 'var(--bad-ink)' : v < 0 ? 'var(--good-ink)' : '';
  const nMat = d.dish_material_count != null ? d.dish_material_count : 0, nNew = d.new_dish_material_count != null ? d.new_dish_material_count : 0;
  const nHi = (d.dishes || []).filter(x => x.spike && x.material).length;
  const tiles = [
    tile('', 'ลูกค้า ช่วงนี้', sm.customers ? fmtN(sm.customers) : '—', esc(pctTxt), { title: sm.customers_per_day != null
      ? `ต่อวัน ${fmtN(sm.customers_per_day)} คน${sm.base_customers_per_day != null ? ` · ${vsBase ? 'ช่วงเทียบ' : 'ปกติ'} ${fmtN(sm.base_customers_per_day)} คนต่อวัน` : ''}` : '' }),
    tile('spikeMat', 'เมนูที่ถึงเกณฑ์', `${fmtN(nMat)} <span class="sp-tile-u">เมนู</span>`, `เมนูใหม่ ${fmtN(nNew)} · พุ่ง ≥${_spikeThr(d)}× ${fmtN(nHi)}`,
      { title: `เกณฑ์ เมนูเดิม ${_spikeFloorTxt(d)} · เมนูใหม่ ${_spikeNewFloorTxt(d)}` }),
    cv && dt.net_cost != null
      ? tile('spikeCost', 'ต้นทุนเพิ่มสุทธิ', `${sg(dt.net_cost)}฿${fmt(Math.abs(dt.net_cost))}`,
        `เพิ่ม ฿${fmt(dt.up_cost || 0)} · ลด ฿${fmt(Math.abs(dt.down_cost || 0))}`,
        { ink: ink(dt.net_cost), cls: 'sp-tile-cost',
          q: ' <button type="button" id="spikeCostQ" class="ui-q" aria-label="คำอธิบาย ต้นทุนเพิ่มสุทธิ" aria-haspopup="dialog" data-help="hlp-dailysales-spike-cost" title="คำอธิบาย">?</button>',
          title: `${dt.net_share != null ? `${sg(dt.net_share)}${_spikePct(Math.abs(dt.net_share), 2)}% ของต้นทุนอาหาร ${_spikeBaht(d.food_cost_total)} · ` : ''}`
            + `${dt.net_pp != null ? `${sg(dt.net_pp)}${_spikePct(Math.abs(dt.net_pp), 2)} จุดของต้นทุนอาหาร % · ` : ''}ยอดขายสุทธิ ${_spikeBaht(d.revenue_total)}` })
      : tile('spikeExcess', 'จานเพิ่มสุทธิ', dt.net_excess != null ? `${sg(dt.net_excess)}${fmtN(Math.abs(Math.round(dt.net_excess)))} <span class="sp-tile-u">จาน</span>` : '—',
        dt.up_excess != null ? `เพิ่ม ${fmtN(Math.round(dt.up_excess))} · ลด ${fmtN(Math.abs(Math.round(dt.down_excess || 0)))}` : '', { ink: ink(dt.net_excess || 0) }),
    tile('', 'tier ที่ดันมากสุด', sm.tier_top ? esc(sm.tier_top.label) : '—', sm.tier_top ? `+${fmtN(sm.tier_top.excess)} จาน` : 'ไม่มีข้อมูลแยก tier'),
  ];
  st.innerHTML = tiles.join('');
  st.hidden = false;
}
// DV3 (owner 2026-10-25, "อย่าลืมให้ filter ได้ด้วย") · the card keeps its own period chips and FOLLOWS the top bar's branch: a change of the
// top bar's branch (or the phone's branch chip, the same #selBranch) moves this card's select to it; the card's own select then narrows the
// card alone until the top bar changes again. A pinned login (PR-K) has no select (data-branch-picker): the top bar's hidden branch rules.
let _spikeTopBr = null;
async function renderMenuSpike(focusDate) {
  const el = document.getElementById('dsSpike'); if (!el) return;
  if (focusDate) _spikeFocus = focusDate;     // remember focus across branch-picker changes
  _spikeChips();
  // populate the local branch picker once
  const bsel = /** @type {HTMLSelectElement|null} */ (document.getElementById('spikeBranch'));
  if (bsel && !_spikeBranchesLoaded) {
    try {
      const bl = await api(`${API}/api/branches`);
      const names = (bl || []).map(b => (typeof b === 'string' ? b : (b.name || b.branch))).filter(Boolean)
        .filter(n => n !== 'ALL').sort();
      bsel.innerHTML = '<option value="">ทุกสาขา</option>' + names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
      _spikeBranchesLoaded = true;
    } catch (e) {}
  }
  const topBr = getBranch() || '';
  if (bsel && topBr !== _spikeTopBr) {
    if (topBr && ![...bsel.options].some(o => o.value === topBr)) bsel.insertAdjacentHTML('beforeend', `<option value="${esc(topBr)}">${esc(topBr)}</option>`);
    bsel.value = topBr;
    _spikeTopBr = topBr;
  }
  const mode = _spikeMode.m;
  if (mode === 'range' && !(_spikeMode.f && _spikeMode.t)) { spikeSetMode('range'); el.innerHTML = '<p class="text-xs py-2" style="color:var(--text-muted)">เลือกช่วงวันที่ จาก–ถึง แล้วกด แสดง</p>'; return; }
  el.innerHTML = '<p class="text-xs py-2" style="color:var(--text-muted)">กำลังโหลด…</p>';
  const p = new URLSearchParams(); p.set('threshold', '1.5'); p.set('limit', '500');
  if (mode !== 'day') p.set('period', mode);
  if (mode === 'range') {
    p.set('date_from', _spikeMode.f); p.set('date_to', _spikeMode.t);
    if (_spikeMode.bk && _spikeMode.bk !== 'normal' && _spikeMode.bf && _spikeMode.bt) { p.set('base_from', _spikeMode.bf); p.set('base_to', _spikeMode.bt); }
  }
  else if (_spikeFocus) p.set('date', _spikeFocus);
  // one branch chosen (this card's select, which follows the top bar): the branch view - rows per dish of that branch, its name in the chip
  const oneBr = bsel ? (bsel.value || '') : topBr;
  if (oneBr) p.set('branch', oneBr);
  if (getGroup()) p.set('group', getGroup());
  const seq = ++_spikeSeq;
  let d;
  try { d = await api(`${API}/api/sales/menu-spike?${p}`); } catch (e) { if (seq === _spikeSeq) el.innerHTML = ''; return; }
  if (seq !== _spikeSeq) return;                 // a newer choice is already on its way
  const rangeEl = document.getElementById('spikeRange'), cntEl = document.getElementById('spikeCount');
  if (d && typeof d.detail === 'string') {       // 400: the custom range is not valid
    _spikeLast = null;
    const st = document.getElementById('spikeStrip'); if (st) { st.hidden = true; st.innerHTML = ''; }
    if (rangeEl) rangeEl.textContent = '';
    if (cntEl) cntEl.textContent = '';
    el.innerHTML = `<p class="text-xs py-2" style="color:var(--bad-ink)">${esc(d.detail)}</p>`;
    return;
  }
  _spikeLast = d; _spikeOneBr = oneBr;
  _spikeDraw(d, oneBr);
}
// DV3 (phone table review 2026-10-25, top-10 #3) · two parts, one row per DISH:
//   เมนูเดิม · ดันต้นทุนเพิ่มสุทธิ (d.dishes): chain view = every branch summed, NET extra cost (ups minus downs), สาขาที่ดัน under the name;
//   one branch = rows per dish of that branch with its tiers. The rows at or above the floor (material, first `top`) show first; ดูทั้งหมด (N)
//   opens every dish above its normal (remembered on this device). ต้นทุนเพิ่มสุทธิ carries its share of the food cost, สะสม the cumulative %.
//   เมนูใหม่และเมนูที่กลับมาขาย (d.new_dishes): grouped per dish the same way, its own floor, FOLDED by default (remembered).
// The rows per branch x menu and the raw-plates list (แบบเดิม) are in the CSV only.
function _spikeDraw(d, oneBr) {
  const el = document.getElementById('dsSpike'); if (!el) return;
  const rangeEl = document.getElementById('spikeRange'), cntEl = document.getElementById('spikeCount');
  const per = !!d.period && d.period !== 'day';
  if (per && d.latest_day) ['spikeFrom', 'spikeTo', 'spikeBaseFrom', 'spikeBaseTo'].forEach(id => { const x = document.getElementById(id); if (x) x.max = d.latest_day; });
  const vsBase = !!(d.base && d.base.kind === 'range');
  // the week lists follow the last finished day the answer names (filled before the first answer from the page's focus day)
  if (d.latest_day) [['spikeCurWeek', 'week', 'f', 'pk'], ['spikeBaseWeek', 'week', 'bf', 'bk']].forEach(([id, k, key, kk]) => {
    const sel = document.getElementById(id);
    if (sel && sel.dataset.last !== d.latest_day) { const keep = sel.value || (_spikeMode[kk] === k ? _spikeMode[key] : ''); _spikeFillWeeks(sel, keep); sel.dataset.last = d.latest_day; }
  });
  if (rangeEl) {
    const pr = per ? periodRange('range', { from: d.date_from, to: d.date_to }) : periodRange('day', d.date);
    // PR-TD: a chosen comparison period shows both ranges ("13–19 ต.ค. เทียบ 6–12 ต.ค.")
    const span = vsBase ? `${_spikeSpan(d.date_from, d.date_to)} เทียบ ${_spikeSpan(d.base.from, d.base.to)}` : (pr ? pr.label : '');
    const txt = [oneBr, span].filter(Boolean).join(' · '); rangeEl.textContent = txt; rangeEl.hidden = !txt;
  }
  const subEl = document.getElementById('spikeSubTxt');
  const thr = _spikeThr(d);
  if (subEl) subEl.textContent = vsBase ? `เทียบต่อหัวกับช่วง ${_spikeSpan(d.base.from, d.base.to)} · ป้าย พุ่ง = ≥${thr}×` : `เทียบต่อหัวกับปกติ · ป้าย พุ่ง = ≥${thr}×`;
  const cv = !!d.cost_view, top = d.top || 15, chain = (d.view || (oneBr ? 'branch' : 'chain')) === 'chain';
  if (cntEl) cntEl.textContent = '';
  _spikeStrip(d, vsBase);
  const nowW = per ? 'ช่วงนี้' : 'วันนี้', normW = vsBase ? 'ช่วงเทียบ' : 'ปกติ';
  const num1 = v => v == null ? '—' : Number(v).toLocaleString('th-TH', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const thead = cols => `<thead class="sticky top-0" style="background:var(--bg-card)"><tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">${cols}</tr></thead>`;
  const pctOf = v => v == null ? '—' : v > 0 && v < 0.05 ? '&lt;0.1' : _spikePct(v, v >= 10 ? 0 : 1);
  // ── เมนูเดิม · by dish ──
  const dsAll = d.dishes || [], dMat = dsAll.filter(x => x.material);
  const nDish = d.dish_count != null ? d.dish_count : dsAll.length, nMat = d.dish_material_count != null ? d.dish_material_count : dMat.length;
  const ds = _spikeAll ? dsAll : dMat.slice(0, top);
  const floorTxt = _spikeFloorTxt(d);
  const hiChip = x => x.spike ? ` <span class="sp-hi-chip" title="${esc(`สูงกว่า${normW}ตั้งแต่ ${thr} เท่า และอย่างน้อย 5 จาน${chain ? ` · พุ่งที่ ${fmtN(x.spike_branches)} สาขา` : ''}`)}">พุ่ง ≥${thr}×</span>` : '';
  const subLine = x => {
    if (chain) {
      const t = _spikePushTxt(x, cv);
      return t ? `<div class="sp-push" data-ph-line="">สาขาที่ดัน: ${esc(t)}</div>` : '';
    }
    const tt = x.tiers ? _spikeTierText(x) : '';
    return (tt ? `<div class="sp-push" data-ph-line="">เพิ่มจาก tier: ${esc(tt)}</div>` : '')
      + (x.rate != null ? `<div class="sp-push" data-ph-line="">${num1(x.rate)} ต่อ 100 ลูกค้า · ${normW} ${num1(x.normal_rate)}</div>` : '');
  };
  const nameTd = x => `<td class="sp-name"><span class="sp-menu" title="${esc(x.menu)}">${esc(x.menu)}</span>${hiChip(x)}${subLine(x)}</td>`;
  const netTd = x => {
    if (x.uncosted) return '<td class="sp-num"><span class="sp-nocost">ยังไม่มีต้นทุน</span></td>';
    return `<td class="sp-num sp-cost-td" title="${esc(`ต้นทุนเพิ่มสุทธิ ${_spikeBaht(x.net_cost)} · ${x.net_pp != null ? _spikePct(x.net_pp, 2) + ' จุดของต้นทุนอาหาร %' : ''}`)}">`
      + `<div class="sp-v" data-ph-line="hi" style="color:var(--bad-ink)">${_spikeBaht(Math.round(x.net_cost))}</div>`
      + `<div class="sp-perday" data-ph-line="">${pctOf(x.cost_share)}% ของต้นทุนอาหาร</div></td>`;
  };
  const cumTd = x => `<td class="sp-num"><span class="sp-v sp-v2">${x.cum_pct != null ? _spikePct(x.cum_pct, 0) + '%' : '—'}</span></td>`;
  const platesTd = x => `<td class="sp-num" title="${esc(`${nowW} ${fmtN(x.today)} จาน · ${normW} ${fmtN(x.expected)} จาน`)}"><div class="sp-v" data-ph-line="">+${fmtN(x.excess)}${x.thin_base && cv ? _spikeThinMk(vsBase) : ''}</div>`
    + `<div class="sp-perday" data-ph-line="">${fmtN(x.today)} · ${normW} ${fmtN(x.expected)}</div></td>`;
  const xTd = x => `<td class="sp-num">${x.x != null ? `<div class="sp-v" data-ph-line="${x.spike ? 'hi' : ''}" style="color:${x.spike ? 'var(--bad-ink)' : 'var(--text-secondary)'};font-weight:700">×${_spikePct(x.x, 2)}${x.thin_base ? _spikeThinMk(vsBase) : ''}</div>`
    + (x.pct != null ? `<div class="sp-perday" data-ph-line="">${x.pct >= 0 ? '+' : '−'}${fmtN(Math.abs(x.pct))}%</div>` : '') : '<div class="sp-v" data-ph-line="" style="color:var(--text-muted)">—</div>'}</td>`;
  const allBtn = (id, on, n, fn, onTxt) => `<button type="button" class="sp-all" id="${id}" aria-expanded="${on ? 'true' : 'false'}" onclick="${fn}()">${on ? onTxt : `ดูทั้งหมด (${fmtN(n)})`}</button>`;
  const oldBtn = dsAll.length > dMat.slice(0, top).length ? allBtn('spikeAllBtn', _spikeAll, nDish, 'spikeToggleAll', 'แสดงเฉพาะที่ถึงเกณฑ์') : '';
  const oldHtml = !dsAll.length
    ? `<p class="sp-empty">ไม่พบเมนูเดิมที่สูงกว่า${normW}ใน${nowW}</p>`
    : (!ds.length ? `<p class="sp-empty" id="spikeNoMat">ไม่มีเมนูที่ถึงเกณฑ์ (${esc(floorTxt)})</p>`
      : `<div class="sp-box sp-box-top"><table class="sp-tbl sp-fixed w-full${cv ? ' sp-costed' : ''}" id="spikeTable" data-view="${chain ? 'chain' : 'branch'}">${_spikeCols(cv ? [3, 47, 20, 10, 20] : [3, 47, 20, 15, 15])}${thead(cv
        ? `<th class="sp-rn">#</th><th class="sp-name-h">เมนู</th><th class="text-right">ต้นทุนเพิ่มสุทธิ</th><th class="text-right">สะสม</th><th class="text-right">+จาน <span class="sp-unit">(สุทธิ)</span></th>`
        : `<th class="sp-rn">#</th><th class="sp-name-h">เมนู</th><th class="text-right">+จาน <span class="sp-unit">(สุทธิ)</span></th><th class="text-right">สูงกว่า</th><th class="text-right">สะสม</th>`)}<tbody>`
        + ds.map((x, i) => `<tr class="border-t border-slate-100 hover:bg-indigo-50/40${x.material ? '' : ' sp-under'}">
      <td class="sp-rn tabular-nums" style="color:var(--text-muted)">${i + 1}</td>
      ${nameTd(x)}
      ${cv ? netTd(x) + cumTd(x) + platesTd(x) : platesTd(x) + xTd(x) + cumTd(x)}
    </tr>`).join('') + '</tbody></table></div>') + oldBtn;
  // ── เมนูใหม่ · by dish, folded ──
  const nd = d.new_dishes || [], ndMat = nd.filter(x => x.material);
  const nNewD = d.new_dish_count != null ? d.new_dish_count : nd.length, nNewMat = d.new_dish_material_count != null ? d.new_dish_material_count : ndMat.length;
  const nds = _spikeNewAll ? nd : ndMat.slice(0, top);
  // PR-SX · more than half the listed new dishes without a dish cost: no ต้นทุน column (a wall of ยังไม่มีต้นทุน), a chip in the header
  const ndCosted = nds.filter(x => !x.uncosted).length;
  const ndCostCol = cv && nds.length > 0 && (nds.length - ndCosted) * 2 <= nds.length;
  const ndCostChip = cv && nds.length > 0 && !ndCostCol
    ? ` <span class="sp-gap-chip" id="spikeNewCostChip" title="${esc(`มีต้นทุน ${fmtN(ndCosted)} จาก ${fmtN(nds.length)} รายการ · ดูต้นทุนรายเมนูได้ในไฟล์ CSV`)}">ต้นทุนเมนูใหม่ยังไม่ครบ (${fmtN(ndCosted)}/${fmtN(nds.length)})</span>` : '';
  const backChip = x => !x.returning ? '' : chain
    ? ` <span class="sp-back-chip" title="${esc(`เมนูเดิมที่กลับมาขายที่ ${fmtN(x.returning)} สาขา · นับเป็นเมนูใหม่ 28 วันนับจากวันที่กลับมาขาย`)}">กลับมาขาย ${fmtN(x.returning)} สาขา</span>`
    : _spikeBackChip({ returning: true, returned_on: x.returned_on || x.first_sold, first_sold_ever: x.first_sold_ever });
  const newSub = x => chain
    ? `<div class="sp-push" data-ph-line="">ขาย ${fmtN(x.branches)} สาขา: ${esc(_spikeSoldTxt(x))}</div>`
    : `<div class="sp-push" data-ph-line="">เริ่มขาย ${esc(x.first_sold_th || fmtDateTh(x.first_sold))} · ${fmtN(x.age_days)} วัน</div>`;
  const trendTd = x => {
    const t = x.trend_pct;
    if (t == null) return '<td class="sp-num"><span style="color:var(--text-muted)">—</span></td>';
    const ink = t > 0 ? 'var(--good-ink)' : t < 0 ? 'var(--bad-ink)' : 'var(--text-secondary)';
    return `<td class="sp-num"><span class="sp-v" style="color:${ink};font-weight:700">${t > 0 ? '▲ +' : t < 0 ? '▼ −' : ''}${fmtN(Math.abs(t))}%</span></td>`;
  };
  const newBtn = nd.length > ndMat.slice(0, top).length ? allBtn('spikeNewAllBtn', _spikeNewAll, nNewD, 'spikeNewToggleAll', 'แสดงเฉพาะที่ถึงเกณฑ์') : '';
  const newHtml = !nd.length
    ? `<p class="sp-empty">ไม่มีเมนูใหม่ที่ขายใน${nowW}</p>`
    : (!nds.length ? `<p class="sp-empty">ไม่มีเมนูใหม่ที่ถึงเกณฑ์ (${esc(_spikeNewFloorTxt(d))})</p>`
      : `<div class="sp-box sp-box-top"><table class="sp-tbl sp-fixed w-full${ndCostCol ? ' sp-costed' : ''}" id="spikeNewTable">${_spikeCols(ndCostCol ? [3, 41, 14, 10, 10, 11, 11] : [3, 45, 12, 13, 14, 13])}${thead(`
    <th class="sp-rn">#</th><th class="sp-name-h">เมนู</th>${ndCostCol ? '<th class="text-right">ต้นทุน <span class="sp-unit">(ช่วงนี้)</span></th>' : ''}
    <th class="text-right">จาน/วัน</th><th class="text-right">${nowW} <span class="sp-unit">(จาน)</span></th><th class="text-right">ต่อ 100 ลูกค้า</th><th class="text-right">แนวโน้ม</th>`)}<tbody>`
        + nds.map((x, i) => `<tr class="border-t border-slate-100 hover:bg-indigo-50/40${x.returning ? ' sp-back-row' : ''}">
      <td class="sp-rn tabular-nums" style="color:var(--text-muted)">${i + 1}</td>
      <td class="sp-name"><span class="sp-menu" title="${esc(x.menu)}">${esc(x.menu)}</span>${backChip(x)}${newSub(x)}</td>
      ${ndCostCol ? (x.uncosted ? '<td class="sp-num"><span class="sp-nocost">ยังไม่มีต้นทุน</span></td>'
        : `<td class="sp-num"><span class="sp-v">${_spikeBaht(Math.round(x.period_cost))}</span></td>`) : ''}
      <td class="sp-num"><span class="sp-v">${num1(x.per_day)}</span></td>
      <td class="sp-num"><span class="sp-v2">${fmtN(x.qty)}</span></td>
      <td class="sp-num"><span class="sp-v2">${num1(x.rate)}</span></td>
      ${trendTd(x)}
    </tr>`).join('') + '</tbody></table></div>') + newBtn;
  const oldTitle = cv ? 'เมนูเดิม · ดันต้นทุนเพิ่มสุทธิ' : 'เมนูเดิม · จานเพิ่มสุทธิ';
  el.innerHTML = `<section class="sp-sec sp-sec-old" aria-label="${oldTitle}">
      <h3 class="sp-h">${oldTitle}${chain ? ' <span class="sp-scope">รวมทุกสาขา</span>' : ''} <span class="sp-n" id="spikeOldN" title="${esc(`เกณฑ์ ${floorTxt} · ทั้งหมด ${fmtN(nDish)} เมนูที่สูงกว่า${normW}`)}">(${fmtN(nMat)} ถึงเกณฑ์ · ทั้งหมด ${fmtN(nDish)})</span>${cv ? '<span class="sp-sort">เรียงตามต้นทุนเพิ่มสุทธิ</span>' : '<span class="sp-sort">เรียงตามจานเพิ่มสุทธิ</span>'}</h3>${oldHtml}</section>
    <section class="sp-sec sp-sec-new" aria-label="เมนูใหม่">
      <h3 class="sp-h"><span class="sp-long">เมนูใหม่และเมนูที่กลับมาขาย</span><span class="sp-short">เมนูใหม่</span>
        <button type="button" class="ui-q" aria-label="คำอธิบาย" aria-haspopup="dialog" data-help="hlp-dailysales-spike-new" title="คำอธิบาย">?</button>
        <span class="sp-n" id="spikeNewN" title="${esc(`เกณฑ์ ${_spikeNewFloorTxt(d)} · ทั้งหมด ${fmtN(nNewD)} เมนู`)}">(${fmtN(nNewMat)} ถึงเกณฑ์ · ทั้งหมด ${fmtN(nNewD)})</span>${ndCostChip}
        <button type="button" class="sp-fold" id="spikeNewFold" aria-controls="spikeNewBody" aria-expanded="${_spikeNewOpen ? 'true' : 'false'}" onclick="spikeNewFold()">${_spikeNewOpen ? 'ซ่อน' : 'แสดง'}</button></h3>
      <div id="spikeNewBody"${_spikeNewOpen ? '' : ' hidden'}>${newHtml}</div></section>`;
  if (typeof phCardsSchedule === 'function') phCardsSchedule();
}

// ── #10 ต้นทุน/หัว รายสาขา (BoM) ── PR-PP3: the top bar's period (วัน · สัปดาห์ · เดือน · ปี), to date through the last closed day,
// with เทียบช่วงก่อน + เทียบปีก่อน (cost: down = good; food-cost % in จุด). A window with no cost source shows "—", its reason in "?".
async function loadCostHead(force) {
  const G = gPeriodPick();
  const gran = ['day', 'week', 'month', 'year'].includes(G.kind) ? G.kind : 'month';
  const _fk = _filterKey() + '|' + gran + '|' + (G.pick || '');
  if (!force && _tabLastKey.costhead === _fk) return;
  _tabLastKey.costhead = _fk;
  const p = new URLSearchParams(); p.set('gran', gran);
  if (G.pick) p.set('date', G.pick);
  if (getBranch()) p.set('branch', getBranch());
  if (getGroup()) p.set('group', getGroup());
  let d;
  try { d = await api(`${API}/api/sales/cost-per-head?${p}`); } catch (e) { return; }
  const r = periodOf(d);
  periodChip(r, true);
  document.getElementById('chCmpNote').innerHTML = periodQ(r, '%');
  const t = d.total || {};
  const avgCostPct = t.cost_pct;   // chain food-cost ratio = benchmark
  const cpColor = cp => fcTargetCol(cp);   // ONE food-cost target (owner 2026-10-25)
  const prevOf = k => k === 'cost_per_head' ? t.prev_cost_per_head : k === 'cost_pct' ? t.prev_cost_pct : null;
  const lyOf = k => k === 'cost_per_head' ? t.ly_cost_per_head : k === 'cost_pct' ? t.ly_cost_pct : null;
  document.getElementById('chKpis').innerHTML = [
    // a period before the cost data starts comes back null — "—", never a fake ฿0
    { l: 'ต้นทุน/หัว (BoM)', v: t.cost_per_head != null ? fmt(t.cost_per_head) : '—', c: '#ef4444', k: 'cost_per_head', unit: '%' },
    { l: 'ต้นทุน% (food cost)', v: t.cost_pct != null ? t.cost_pct.toFixed(1) + '%' : '—', c: cpColor(t.cost_pct), k: 'cost_pct', unit: 'pp' },
    { l: 'รายได้/หัว', v: t.rev_per_head != null ? fmt(t.rev_per_head) : '—', c: '#0891b2' },
    { l: 'GP/หัว', v: t.gp_per_head != null ? fmt(t.gp_per_head) : '—', c: '#10b981' },
    { l: 'ลูกค้า (Pax)', v: fmtN(t.customers || 0), c: '#7a66b8' },
  ].map((k, i) => k.k
    ? periodKpi({ l: k.l, v: k.v, c: k.c, cur: t[k.k], prev: prevOf(k.k), ly: lyOf(k.k), unit: k.unit, upGood: false, range: r, q: i === 0 })
    : `<div class="card p-4 pkpi" style="border-top:3px solid ${k.c}"><p class="text-[11px] uppercase tracking-wide" style="color:var(--text-muted)">${k.l}</p><p class="text-2xl font-bold" style="color:${textInk(k.c)}">${k.v}</p></div>`).join('');

  const branches = d.branches || [];
  // Insight: worst food-cost ratio (margin risk) + best efficiency, vs chain avg.
  const withCp = branches.filter(x => x.cost_pct != null);
  const ins = document.getElementById('chInsight');
  if (withCp.length >= 2 && avgCostPct != null) {
    const worst = withCp[0];                          // already sorted by cost_pct desc
    const best  = withCp[withCp.length - 1];
    ins.innerHTML = `<div class="card p-3 text-xs flex flex-wrap gap-x-6 gap-y-1" style="background:var(--bg-subtle)">
      <span>⚠ <b>${esc(worst.branch)}</b> ต้นทุนสูงสุด <b style="color:var(--bad-ink)">${worst.cost_pct}%</b> (รายได้/หัว ${fmt(worst.rev_per_head)}, GP/หัว ${fmt(worst.gp_per_head)}) · สูงกว่าเฉลี่ยเครือ ${(worst.cost_pct - avgCostPct).toFixed(1)} จุด</span>
      <span>✓ <b>${esc(best.branch)}</b> ประหยัดสุด <b style="color:var(--good-ink)">${best.cost_pct}%</b></span>
      <span style="color:var(--text-muted)">เฉลี่ยเครือ ${avgCostPct}%</span></div>`;
  } else { ins.innerHTML = ''; }

  const sameBase = !!r && r.prev[0] === r.ly[0] && r.prev[1] === r.ly[1];
  const thP = document.getElementById('chThPrev'), thL = document.getElementById('chThLy');
  if (thP) { thP.title = r ? r.prev_label_th : ''; thP.hidden = sameBase; }
  if (thL) thL.title = r ? r.ly_label_th : '';
  const why = d.reasons || {};
  document.getElementById('chBody').innerHTML = branches.map(x => {
    const dev = (x.cost_pct != null && avgCostPct != null) ? (x.cost_pct - avgCostPct) : null;
    const devHtml = dev == null ? '<span class="text-slate-300">—</span>'
      : `<span style="color:${dev > 1 ? 'var(--bad-ink)' : dev < -1 ? 'var(--good-ink)' : 'var(--text-muted)'}">${dev > 0 ? '+' : ''}${dev.toFixed(1)} จุด</span>`;
    const rowBg = (dev != null && dev > 1) ? 'background:rgba(239,68,68,0.05)' : '';
    return `<tr class="border-t border-slate-100 hover:bg-indigo-50/40" style="${rowBg}">
      <td class="py-1.5 pr-2 font-medium">${esc(x.branch)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmtN(x.customers)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${x.rev_per_head != null ? fmt(x.rev_per_head) : '—'}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold">${x.cost_per_head != null ? fmt(x.cost_per_head) : '—'}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-bold" style="color:${textInk(cpColor(x.cost_pct))}">${x.cost_pct != null ? x.cost_pct + '%' : '—'}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums" style="color:var(--good-ink)">${x.gp_per_head != null ? fmt(x.gp_per_head) : '—'}</td>
      ${sameBase ? '' : `<td class="py-1.5 pr-2 text-right tabular-nums">${periodPctDelta(x.delta_pct, { upGood: false, title: r ? `${r.label_th} เทียบ ${r.prev_label_th}${why.prev ? ' · ' + why.prev : ''}` : '' })}</td>`}
      <td class="py-1.5 pr-2 text-right tabular-nums">${periodPctDelta(x.ly_delta_pct, { upGood: false, title: r ? `${r.label_th} เทียบ ${r.ly_label_th}${why.ly ? ' · ' + why.ly : ''}` : '' })}</td>
      <td class="py-1.5 text-right tabular-nums">${devHtml}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="${sameBase ? 8 : 9}" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>`;
  // DV5 · the answer strip: the branches whose cost per head above the chain's costs the most baht (the gap × their customers), and the
  // chain's cost per head against the period before; a line opens the Top 60 ranking (the dishes that push each branch up)
  const cph0 = t.cost_per_head, excess = x => x.cost_per_head != null && cph0 != null ? (x.cost_per_head - cph0) * (x.customers || 0) : null;
  const hot = branches.filter(x => (excess(x) || 0) > 0).sort((a, b) => excess(b) - excess(a));
  const hotLine = (x, k) => x && ansLine(k, `${x.branch} ${dvBaht(excess(x))}`, `ต้นทุน/หัว ฿${fmt(x.cost_per_head)} · ${dvBaht(x.cost_per_head - cph0)}/หัว เทียบเฉลี่ย`, 'bad', '#chBody');
  const pv = sameBase ? t.ly_cost_per_head : t.prev_cost_per_head;
  pageAnswer('costhead', [
    !getBranch() && hotLine(hot[0], 'ต้นทุน/หัวเกินเฉลี่ยมากสุด (บาท)'), !getBranch() && hotLine(hot[1], 'อันดับถัดไป'),
    cph0 != null && ansLine(getBranch() ? 'ต้นทุน/หัวของสาขา' : 'ต้นทุน/หัวทั้งเครือ', `฿${fmt(cph0)}${pv ? ' · ' + ansPct((cph0 - pv) / pv * 100) : ''}`,
      `${pv ? `เทียบ${sameBase ? 'ปีก่อน' : 'ช่วงก่อน'} ฿${fmt(pv)} · ` : ''}เมนูที่ดันต้นทุน: Top 60`, pv ? ansTone(cph0 - pv, false) : '', '@costheadtop60'),
  ]);
}

// ══ P&L ต่อบิล (per-bill profit, COGS = BoM) ══
// PR-PP3: the period is the top bar's (วัน · สัปดาห์ · เดือน; per-bill COGS is a rolling window), to date through the last
// closed day; the totals compare with the same days of the period before / the year before where the window holds them.
let _pbGran = 'day', _pbView = 'gm', _pbData = null, _pbDrillIdx = null, _pbPick = '';
function pbSetView(v) {
  _pbView = v;
  document.querySelectorAll('#pbViewTrack .gran-btn').forEach(b => b.classList.toggle('gran-active', b.dataset.v === v));
  if (_pbData) renderPnlBill(_pbData);
}
async function loadPnlBill(force) {
  const G = gPeriodPick();
  _pbGran = ['day', 'week', 'month'].includes(G.kind) ? G.kind : 'month';
  const dv = _pbPick = G.pick || '';
  const _fk = _filterKey() + '|' + _pbGran + '|' + dv;
  if (!force && _tabLastKey.pnlbill === _fk) return;
  _tabLastKey.pnlbill = _fk;
  const p = new URLSearchParams(); p.set('gran', _pbGran);
  if (dv) p.set('date', dv);
  if (getBranch()) p.set('branch', getBranch());
  if (getGroup()) p.set('group', getGroup());
  let d;
  try { d = await api(`${API}/api/reports/pnl-bill?${p}`); } catch (e) { return; }
  periodChip(periodOf(d), true);
  _pbData = d;
  _pbDrillIdx = null; pbCloseDrill();   // a fresh period/filter closes any open drill
  renderPnlBill(d);
}
// Zoom dive-in: drill into a single gross-margin band → its bills + an insight summary
async function pbDrill(idx, keepOpen) {
  _pbDrillIdx = idx;
  document.querySelectorAll('#pbDist .pb-band').forEach(el => el.classList.toggle('pb-band-active', +el.dataset.idx === idx));
  const dv = _pbPick;
  const p = new URLSearchParams(); p.set('idx', idx); p.set('gran', _pbGran);
  if (dv) p.set('date', dv);
  if (getBranch()) p.set('branch', getBranch());
  if (getGroup()) p.set('group', getGroup());
  let d;
  try { d = await api(`${API}/api/reports/pnl-bill-drill?${p}`); } catch (e) { return; }
  const cogsView = _pbView === 'cogs';
  const panel = document.getElementById('pbDrill');
  const st = d.stat || {};
  const bandLbl = cogsView
    ? (idx < 0 ? 'ขาดทุน (COGS > 100%)' : `COGS ${100 - d.hi}–${100 - d.lo}%`)
    : `GM ${esc(d.label || '')}`;
  document.getElementById('pbDrillBand').textContent = bandLbl + (d.capped ? ' (แสดง 500 บิลแรก)' : '');
  document.getElementById('pbDrillMetric').textContent = cogsView ? 'COGS%' : 'GM%';
  const gpBill = st.n ? Math.round(st.gp / st.n) : 0;
  const stats = [
    { l: 'จำนวนบิล', v: fmtN(st.n || 0), c: '#0891b2' },
    { l: cogsView ? 'COGS เฉลี่ย/บิล' : 'GM เฉลี่ย', v: (st.avg_gm != null ? (cogsView ? (100 - st.avg_gm).toFixed(1) : st.avg_gm.toFixed(1)) + '%' : '—'), c: '#4a70b0' },
    { l: 'รายได้รวม', v: fmt(st.rev || 0), c: '#4a70b0' },
    { l: 'COGS รวม (BoM)', v: fmt(st.cogs || 0), c: '#ef4444' },
    { l: 'กำไรรวม', v: fmt(st.gp || 0), c: '#10b981' },
    { l: 'กำไร/บิล', v: fmt(gpBill), c: '#7a66b8' },
    { l: 'ลูกค้า', v: fmtN(st.cust || 0), c: '#f59e0b' },
  ];
  document.getElementById('pbDrillStat').innerHTML = stats.map(k => `<div class="rounded-lg p-2.5" style="background:var(--bg-subtle);border-top:2px solid ${k.c}"><p class="text-[10px] uppercase" style="color:var(--text-muted)">${k.l}</p><p class="text-base font-bold" style="color:${textInk(k.c)}">${k.v}</p></div>`).join('');
  const metricCell = r => {
    if (r.gp_pct == null) return '—';
    const val = cogsView ? (100 - r.gp_pct) : r.gp_pct;
    const color = cogsView ? (val > 100 ? '#ef4444' : val > 70 ? '#f59e0b' : '#10b981')
                           : (r.gp_pct < 0 ? '#ef4444' : r.gp_pct < 30 ? '#f59e0b' : '#10b981');
    return `<span style="color:${textInk(color)}">${val.toFixed(1)}%</span>`;
  };
  document.getElementById('pbDrillBody').innerHTML = (d.bills || []).map((r, i) => `<tr class="border-t border-slate-100 hover:bg-indigo-50/40">
    <td class="py-1.5 pr-2 tabular-nums text-slate-400">${i + 1}</td>
    <td class="py-1.5 pr-2 font-mono text-[10.5px]">${esc(r.receipt)}</td>
    <td class="py-1.5 pr-2">${esc(r.branch)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(r.rev)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmt(r.cogs)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${r.rev - r.cogs < 0 ? 'var(--bad-ink)' : 'var(--good-ink)'}">${fmt(Math.round(r.rev - r.cogs))}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${r.disc ? fmt(r.disc) : '—'}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(r.cust)}</td>
    <td class="py-1.5 text-right tabular-nums font-semibold">${metricCell(r)}</td>
  </tr>`).join('') || '<tr><td colspan="9" class="py-4 text-center text-slate-400">ไม่มีบิลในช่วงนี้</td></tr>';
  panel.style.display = 'block';
  if (!keepOpen) panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
function pbCloseDrill() {
  _pbDrillIdx = null;
  const panel = document.getElementById('pbDrill'); if (panel) panel.style.display = 'none';
  document.querySelectorAll('#pbDist .pb-band').forEach(el => el.classList.remove('pb-band-active'));
}
function renderPnlBill(d) {
  const cogsView = _pbView === 'cogs';
  const r = periodOf(d);
  // Per-bill COGS is a rolling ~31-day window. Outside it every figure is 0,
  // which is honest but reads as a broken page — say why, in place.
  const _pbWin = document.getElementById('pbRange');
  if (_pbWin) {
    _pbWin.innerHTML = periodQ(r)
      + (d.out_of_window && d.data_window
          ? ` <span style="color:var(--amber-ink);font-weight:600">· ไม่มีข้อมูลรายบิลของช่วงนี้ (มีเฉพาะ ${fmtDay(d.data_window[0])} → ${fmtDay(d.data_window[1])})</span>`
          : '');
  }
  const s = d.summary || {}, sp = d.summary_prev || null, sl = d.summary_ly || null;
  const pf = (o, k) => o ? (k === 'cogs_pct' ? (o.revenue ? o.cogs / o.revenue * 100 : null) : k === 'cogs_bill' ? (o.bills ? o.cogs / o.bills : null) : k === 'cogs_head' ? (o.customers ? o.cogs / o.customers : null) : o[k]) : null;
  const cogsPct = (s.revenue ? s.cogs / s.revenue * 100 : null);
  const cogsPerBill = (s.bills ? Math.round(s.cogs / s.bills) : null);
  const cogsPerHead = (s.customers ? Math.round(s.cogs / s.customers) : null);
  const kpis = cogsView ? [
    { l: 'จำนวนบิล', v: fmtN(s.bills || 0), sub: 'ลูกค้า ' + fmtN(s.customers || 0) + ' คน', c: '#0891b2', k: 'bills' },
    { l: 'ต้นทุนรวม (COGS·BoM)', v: fmt(s.cogs || 0), sub: 'COGS ' + (cogsPct != null ? cogsPct.toFixed(1) + '% ของรายได้' : '—'), c: '#ef4444', k: 'cogs_pct', unit: 'pp', upGood: false },
    { l: 'COGS/บิล เฉลี่ย', v: fmt(cogsPerBill || 0), sub: 'รายได้รวม ' + fmt(s.revenue || 0), c: '#f59e0b', k: 'cogs_bill', upGood: false },
    { l: 'COGS/หัว เฉลี่ย', v: fmt(cogsPerHead || 0), sub: 'ส่วนลดรวม ' + fmt(s.discount || 0), c: '#7a66b8', k: 'cogs_head', upGood: false },
  ] : [
    { l: 'จำนวนบิล', v: fmtN(s.bills || 0), sub: 'ลูกค้า ' + fmtN(s.customers || 0) + ' คน', c: '#0891b2', k: 'bills' },
    { l: 'กำไรขั้นต้นรวม (BoM)', v: fmt(s.gross_profit || 0), sub: 'GM ' + (s.gp_pct != null ? s.gp_pct + '%' : '—'), c: '#10b981', k: 'gp_pct', unit: 'pp' },
    { l: 'กำไร/บิล เฉลี่ย', v: fmt(s.gp_per_bill || 0), sub: 'รายได้รวม ' + fmt(s.revenue || 0), c: '#7a66b8', k: 'gp_per_bill' },
    { l: 'กำไร/หัว เฉลี่ย', v: fmt(s.gp_per_head || 0), sub: 'ส่วนลดรวม ' + fmt(s.discount || 0), c: '#f59e0b', k: 'gp_per_head' },
  ];
  document.getElementById('pbKpis').innerHTML = kpis.map(k => periodKpi({ l: k.l, v: k.v, c: k.c, sub: k.sub, cur: pf(s, k.k), prev: pf(sp, k.k), ly: pf(sl, k.k),
    unit: k.unit || '%', upGood: k.upGood !== false, range: r, q: false })).join('');
  // distribution bars — fine GM% bands, or mirrored to COGS%-of-revenue
  document.getElementById('pbDistTitle').innerHTML = (cogsView
    ? 'การกระจายต้นทุนต่อบิล (COGS % ของรายได้)' : 'การกระจายอัตรากำไรขั้นต้นต่อบิล (Gross margin / bill)')
    + ' <span class="font-normal normal-case" style="color:var(--text-muted);font-size:11px">· คลิกแถบเพื่อเจาะลึก</span>';
  const distSrc = (d.dist || []).slice();
  const totalN = distSrc.reduce((a, b) => a + (b.n || 0), 0) || 1;
  const totalGP = distSrc.reduce((a, b) => a + (b.gp || 0), 0) || 1;
  // color ramp by margin health (idx -1 loss → red; higher GM → green)
  const bandColor = b => {
    if (b.idx < 0) return '#ef4444';
    const mid = (b.lo + b.hi) / 2;
    return mid < 20 ? '#f97316' : mid < 35 ? '#f59e0b' : mid < 50 ? '#eab308' : mid < 70 ? '#84cc16' : '#10b981';
  };
  // COGS view: relabel each band to its COGS%-of-revenue mirror, render high→low
  const cogsLabel = b => b.idx < 0 ? 'ขาดทุน (>100%)' : `${100 - b.hi}–${100 - b.lo}%`;
  const dist = cogsView ? distSrc.slice().reverse() : distSrc;
  const maxN = Math.max(1, ...dist.map(x => x.n));
  document.getElementById('pbDist').innerHTML = dist.map(x => {
    const lbl = cogsView ? cogsLabel(x) : x.label;
    const pctBills = (x.n / totalN * 100);
    const gpShare = (x.gp / totalGP * 100);
    return `<div class="flex items-center gap-2 mb-1.5 cursor-pointer pb-band ${_pbDrillIdx === x.idx ? 'pb-band-active' : ''}" data-idx="${x.idx}" onclick="pbDrill(${x.idx})"
        title="${esc(lbl)} · ${fmtN(x.n)} บิล (${pctBills.toFixed(1)}%) · กำไรรวม ${fmt(x.gp)} (${gpShare.toFixed(1)}% ของกำไร) · รายได้ ${fmt(x.rev)}">
      <span class="text-[11px] w-20 shrink-0 tabular-nums" style="color:var(--text-secondary)">${esc(lbl)}</span>
      <div class="flex-1 rounded-full h-3.5" style="background:var(--bg-subtle)"><div class="h-3.5 rounded-full" style="width:${x.n / maxN * 100}%;background:${bandColor(x)}"></div></div>
      <span class="text-[11px] tabular-nums w-12 text-right font-semibold" style="color:var(--text-primary)">${fmtN(x.n)}</span>
      <span class="text-[10px] tabular-nums w-10 text-right" style="color:var(--text-muted)">${pctBills.toFixed(0)}%</span>
    </div>`;
  }).join('') || '<p class="text-slate-400 text-center py-4">ไม่มีข้อมูล</p>';
  // re-open the drill panel for the active band after a re-render (e.g. view toggle)
  if (_pbDrillIdx !== null) pbDrill(_pbDrillIdx, true);
  // metric column label + cell renderer (GM% or COGS%)
  const metricLbl = cogsView ? 'COGS%' : 'GM%';
  document.getElementById('pbLossMetric').textContent = metricLbl;
  const metricCell = r => {
    if (r.gp_pct == null) return '—';
    const val = cogsView ? (100 - r.gp_pct) : r.gp_pct;
    const color = cogsView ? (val > 100 ? '#ef4444' : val > 70 ? '#f59e0b' : '#10b981')
                           : (r.gp_pct < 0 ? '#ef4444' : r.gp_pct < 30 ? '#f59e0b' : '#10b981');
    return `<span style="color:${textInk(color)}">${val.toFixed(1)}%</span>`;
  };
  // กำไรต่อบิล รายสาขา — aggregate margin per branch, worst GM% on top.
  document.getElementById('pbWorst').innerHTML = (d.by_branch || []).map(r => {
    const gmCol = r.gm_pct == null ? '#94a3b8' : r.gm_pct < 0 ? '#ef4444' : r.gm_pct < 30 ? '#f59e0b' : '#10b981';
    const rowBg = (r.loss_bills > 0 || (r.gm_pct != null && r.gm_pct < 30)) ? 'background:rgba(239,68,68,0.05)' : '';
    return `<tr class="border-t border-slate-100 hover:bg-rose-50/40" style="${rowBg}">
      <td class="py-1.5 pr-2 font-medium">${esc(r.branch)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmtN(r.bills)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-bold" style="color:${textInk(gmCol)}">${r.gm_pct != null ? r.gm_pct + '%' : '—'}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${r.gp_per_bill != null ? fmt(r.gp_per_bill) : '—'}</td>
      <td class="py-1.5 text-right tabular-nums" style="color:${r.loss_bills > 0 ? 'var(--bad-ink)' : 'var(--text-muted)'}">${r.loss_bills > 0 ? fmtN(r.loss_bills) : '0'}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="5" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
  // ALL loss-making bills · DV2: the cause lines first (a bill can carry more than one cause), then the 20 largest losses; the rest behind
  // "ดูทั้งหมด" (the CSV keeps every row)
  const loss = (d.loss || []).slice().sort((a, b) => (b.cogs - b.rev) - (a.cogs - a.rev));
  const lossOf = r => Math.max(0, (r.cogs || 0) - (r.rev || 0));
  const causes = [
    { l: 'ลูกค้า 1 คน', f: r => (r.cust || 0) <= 1 },
    { l: 'นั่งนาน 2 ชั่วโมงขึ้นไป', f: r => r.dwell_min != null && r.dwell_min >= 120 },
    { l: 'มีส่วนลด', f: r => (r.disc || 0) > 0 },
  ].map(c => { const xs = loss.filter(c.f); return { l: c.l, n: xs.length, v: xs.reduce((a, r) => a + lossOf(r), 0) }; });
  const other = loss.filter(r => !((r.cust || 0) <= 1 || (r.dwell_min != null && r.dwell_min >= 120) || (r.disc || 0) > 0));
  const lossAll = loss.reduce((a, r) => a + lossOf(r), 0) || 1;
  const pcEl = document.getElementById('pbLossCauses');
  if (pcEl) pcEl.innerHTML = loss.length ? `<table class="text-xs w-full mb-3" id="pbCauseTable"><thead><tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">
      <th class="py-1.5 pr-2">สาเหตุ</th><th class="py-1.5 pr-2 text-right">ขาดทุน</th><th class="py-1.5 pr-2 text-right">สัดส่วน</th><th class="py-1.5 text-right">บิล</th></tr></thead><tbody>`
    + causes.concat([{ l: 'อื่น ๆ', n: other.length, v: other.reduce((a, r) => a + lossOf(r), 0) }]).map(c => `<tr class="border-t border-slate-100">
      <td class="py-1.5 pr-2 font-medium">${esc(c.l)}</td><td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:var(--bad-ink)">${fmt(c.v)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${(c.v / lossAll * 100).toFixed(0)}%</td><td class="py-1.5 text-right tabular-nums">${fmtN(c.n)}</td></tr>`).join('')
    + `</tbody></table>` : '';
  const PB_TOP = 20;
  document.getElementById('pbLossMore').innerHTML = dvMoreBtn('pbLoss', loss.length, PB_TOP);
  const ls = document.getElementById('pbLossSummary');
  if (ls) ls.innerHTML = (d.loss_count || 0) > 0
    ? `ขาดทุน <b style="color:var(--bad-ink)">${fmtN(d.loss_count)}</b> บิล · รวม <b style="color:var(--bad-ink)">${fmt(d.loss_total)}</b>${d.loss_capped ? ' · แสดง 2,000 บิลแรก' : ''} · ${((d.loss_count / (s.bills || 1)) * 100).toFixed(2)}% ของบิลทั้งหมด`
    : 'ไม่มีบิลขาดทุนในช่วงนี้';
  document.getElementById('pbLoss').innerHTML = loss.map((r, i) => `<tr class="border-t border-slate-100 hover:bg-rose-50/40 cursor-pointer"${dvMoreAttr('pbLoss', i, loss.length, PB_TOP)} title="ดูเมนูที่สั่งในบิลนี้" onclick='openBillItems(${JSON.stringify({receipt: r.receipt, branch: r.branch, rev: r.rev, cogs: r.cogs, disc: r.disc, cust: r.cust}).replace(/'/g, "&#39;")})'>
    <td class="py-1.5 pr-2 tabular-nums text-slate-400">${i + 1}</td>
    <td class="py-1.5 pr-2 font-mono text-[10.5px] text-sky-600">${esc(r.receipt)}</td>
    <td class="py-1.5 pr-2">${esc(r.branch)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(r.rev)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmt(r.cogs)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:var(--bad-ink)">${fmt(Math.round(r.cogs - r.rev))}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${r.disc ? fmt(r.disc) : '—'}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(r.cust)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums" style="color:${r.dwell_min == null ? 'var(--text-muted)' : r.dwell_min >= 120 ? 'var(--amber-ink)' : 'var(--text-secondary)'}">${r.dwell_min != null ? r.dwell_min + ' น.' : '—'}</td>
    <td class="py-1.5 text-right tabular-nums font-semibold">${metricCell(r)}</td>
  </tr>`).join('') || '<tr><td colspan="10" class="py-4 text-center text-slate-400">— ไม่มีบิลขาดทุน —</td></tr>';
}

// ══ รายงานปฏิบัติการ (Payment · Channel · Void · Table · Promo) ══
let _opMonthsLoaded = false;
async function _opFillMonths() {
  // populate month options from sales months (descending), once
  const sel = document.getElementById('opMonth');
  if (_opMonthsLoaded || !sel) return;
  try {
    const mi = await api(`${API}/api/months`);
    const months = (mi.months || mi || []).slice().sort().reverse();
    sel.innerHTML = months.map(m => `<option value="${m}">${m}</option>`).join('');
    _opMonthsLoaded = true;
  } catch (e) {}
}
async function loadOpsReport(force) {
  const sel = document.getElementById('opMonth');
  await _opFillMonths();
  periodHeadline('phd-opsreport', 'opsreport');   // PR-PP2: the top bar's period
  // Follow the global period filter: in Day/Week granularity pass the global date range
  // (→ backend aggregates the daily tables); in Month granularity use the month picker.
  const gran = (typeof currentGranularity !== 'undefined') ? currentGranularity : 'monthly';
  let useDate = (gran === 'daily' || gran === 'weekly');
  let dateFrom = '', dateTo = '';
  if (useDate) {
    dateFrom = document.getElementById('selDateFrom')?.value || '';
    dateTo   = document.getElementById('selDateTo')?.value   || '';
    if (!dateFrom && !dateTo) useDate = false;   // no concrete range (latest/all) → month fallback
  }
  let mv = '';
  if (!useDate) { mv = sel?.value || ''; }
  if (sel) { sel.disabled = useDate; sel.style.opacity = useDate ? '0.45' : '1'; }   // month picker inert in day/week mode
  const _fk = _filterKey() + '|' + (useDate ? (dateFrom + '~' + dateTo) : mv);
  if (!force && _tabLastKey.opsreport === _fk) return;
  _tabLastKey.opsreport = _fk;
  const p = new URLSearchParams();
  if (useDate) { if (dateFrom) p.set('date_from', dateFrom); if (dateTo) p.set('date_to', dateTo); }
  else if (mv) p.set('month', mv);
  if (getBranch()) p.set('branch', getBranch());
  let d;
  try { d = await api(`${API}/api/reports/ops?${p}`); } catch (e) { return; }
  if (!useDate && d.month && sel && !sel.value) sel.value = d.month;
  document.getElementById('opRange').textContent = d.daily
    ? (d.date_from === d.date_to ? `วันที่ ${d.date_from}` : `${d.date_from} → ${d.date_to}`)
    : (d.month ? `เดือน ${d.month}` : '');
  const tbl = d.table || {};
  const payTot = (d.payment || []).reduce((a, b) => a + (b.amount || 0), 0);
  document.getElementById('opKpis').innerHTML = [
    { l: 'รับชำระรวม', v: fmt(payTot), sub: (d.payment || []).length + ' ช่องทาง', c: '#0891b2' },
    { l: 'โต๊ะหมุนเวียน เฉลี่ย', v: (tbl.avg_turn_min || 0) + ' นาที', sub: fmtN(tbl.tabs || 0) + ' โต๊ะ', c: '#7a66b8' },
    { l: 'ของเสีย/Void (ต้นทุน BoM)', v: fmt((d.void_total || {}).bom_cost || 0), sub: fmtN((d.void_total || {}).qty || 0) + ' รายการ · ขาย ' + fmt((d.void_total || {}).value || 0), c: '#ef4444' },
    { l: 'ส่วนลดโปรโมชัน', v: fmt((d.promo || []).reduce((a, b) => a + (b.discount || 0), 0)), sub: (d.promo || []).length + ' คูปอง', c: '#f59e0b' },
  ].map(k => `<div class="card p-4" style="border-top:3px solid ${k.c}"><p class="text-[11px] uppercase tracking-wide" style="color:var(--text-muted)">${k.l}</p><p class="text-2xl font-bold" style="color:${textInk(k.c)}">${k.v}</p><p class="text-[11px]" style="color:var(--text-muted)">${k.sub}</p></div>`).join('');
  // payment bars
  const pmax = Math.max(1, ...(d.payment || []).map(x => x.amount || 0));
  document.getElementById('opPayment').innerHTML = (d.payment || []).map(x => `
    <div class="flex items-center gap-2 mb-2">
      <span class="text-[11px] w-28 shrink-0 truncate" style="color:var(--text-secondary)" title="${esc(x.code)}">${esc(x.code)}</span>
      <div class="flex-1 rounded-full h-4" style="background:var(--bg-subtle)"><div class="h-4 rounded-full" style="width:${(x.amount || 0) / pmax * 100}%;background:#0891b2"></div></div>
      <span class="text-[11px] tabular-nums w-24 text-right font-semibold" style="color:var(--text-primary)">${fmt(x.amount)}</span>
    </div>`).join('') || '<p class="text-slate-400 text-center py-4">ไม่มีข้อมูล</p>';
  // channel
  const ctot = (d.channel || []).reduce((a, b) => a + (b.bills || 0), 0) || 1;
  document.getElementById('opChannel').innerHTML = (d.channel || []).map(x => `
    <div class="flex items-center justify-between gap-3 p-2.5 rounded-lg mb-2" style="background:var(--bg-subtle)">
      <div><p class="text-xs font-semibold" style="color:var(--text-primary)">${esc(x.label)}</p>
        <p class="text-[10.5px]" style="color:var(--text-muted)">${fmtN(x.bills)} บิล · ${fmtN(x.customers)} คน · ${(x.bills / ctot * 100).toFixed(1)}%</p></div>
      <p class="text-sm font-bold tabular-nums" style="color:var(--indigo)">${fmt(x.revenue)}</p>
    </div>`).join('') || (d.channel_pending
      ? '<p class="text-amber-600 text-center py-4 text-xs">ช่องทางขายรายวันจะแสดงหลัง sync รอบถัดไป</p>'
      : '<p class="text-slate-400 text-center py-4">ไม่มีข้อมูล</p>');
  document.getElementById('opVoid').innerHTML = (d.void || []).map(x => `<tr class="border-t border-slate-100 hover:bg-rose-50/40">
    <td class="py-1.5 pr-2">${esc(x.menu)}</td><td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(x.qty)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmt(x.value)}</td>
    <td class="py-1.5 text-right tabular-nums font-semibold" style="color:var(--bad-ink)">${fmt(x.bom_cost)}</td></tr>`).join('')
    || (d.void_pending
      ? '<tr><td colspan="4" class="py-4 text-center text-amber-600 text-xs">Void รายวันจะแสดงหลัง sync รอบถัดไป</td></tr>'
      : '<tr><td colspan="4" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>');
  const opPromo = document.getElementById('opPromo');   // DV2: the list moved to ส่วนลด (null on this page)
  if (opPromo) opPromo.innerHTML = (d.promo || []).map(x => `<tr class="border-t border-slate-100 hover:bg-amber-50/40">
    <td class="py-1.5 pr-2">${esc(x.name || ('Series ' + x.series))}${x.code && x.code !== x.name ? ` <span class="text-[10px] text-slate-400">${esc(x.code)}</span>` : ''}</td><td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(x.uses)}</td>
    <td class="py-1.5 text-right tabular-nums font-semibold" style="color:var(--amber-ink)">${fmt(x.discount)}</td></tr>`).join('')
    || '<tr><td colspan="3" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
}

// ══════════ Top 60 ต้นทุน/หัว · สาขา Master (เทียบภาพรวม) ══════════
let _chtMonthsLoaded = false;
// ══ วิเคราะห์วัตถุดิบ (Menu Impact) ═══════════════════════════════════════
let _miEnts = null, _miChart = null;
// Last payloads, kept so the dive-in panels can summarise the real numbers
// instead of re-deriving them from the chart's datasets.
let _miLast = null, _ptLast = null;

// Tukey-fence outlier indices for a column of numbers — the same rule the
// dive-in analyser uses (_detectOutliers in js/06-features.js), reused here to
// paint the rows that sit outside the pack. Returns a Set of indices.
function _miOutlierSet(nums) {
  const clean = nums.filter(v => v != null && isFinite(v));
  if (clean.length < 5) return new Set();
  try { return new Set(_detectOutliers(nums.map(v => (v == null ? 0 : v))).indices); }
  catch (e) { return new Set(); }
}

// A tier code as a price label. Server-side labels (tier_labels) win where a
// payload carries them; this is the fallback for the menu-impact rows, which
// send the bare code. Never hard-code a price list here — the Grand Menu
// changeover retired one and this would silently mislabel the survivors.
function _miTierLabel(t) {
  const s = String(t);
  if (s === 'promo') return 'โปร/ส่วนลด';
  if (s === 'child') return 'เด็กล้วน';
  return /^\d+$/.test(s) ? '฿' + Number(s).toLocaleString('en-US') : s;
}

// A magnitude bar for a numeric cell — "how far out" without reading digits.
function _miBar(v, max, color) {
  const w = (!max || v == null) ? 0 : Math.round(Math.min(Math.abs(v) / max, 1) * 26);
  return w < 2 ? '' : `<span class="mi-bar" style="width:${w}px;background:${color}"></span>`;
}
function miFilterOptions() {
  const q = (document.getElementById('miSearch').value || '').trim().toLowerCase();
  const sel = document.getElementById('miEnt');
  if (!_miEnts) return;
  const cur = sel.value;
  const g = _miEnts.groups.map(e =>
    `<option value="${e.ent}">${esc(e.label)} (${e.n_members} เมนู)</option>`).join('');
  const s = _miEnts.skus
    .filter(e => !q || e.label.toLowerCase().includes(q))
    .slice(0, 250)
    .map(e => `<option value="${e.ent}">${esc(e.label)} · ฿${e.cost}</option>`).join('');
  sel.innerHTML = `<optgroup label="กลุ่มวัตถุดิบ">${g}</optgroup><optgroup label="เมนูรายตัว (Top ตามยอดขาย)">${s}</optgroup>`;
  if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
}
// Tier picker options for the chosen range. The Grand Menu (1 Sep 2026) replaced
// the old price list, so a September window must not offer ฿590 — the backend
// decides which prices were on sale, this only renders them. A tier that leaves
// the list (the range moved past the changeover) falls back to "ทุก Tier ราคา".
let _miTierKey = null;
async function miFillTiers(df, dt2) {
  const key = `${df || ''}|${dt2 || ''}`;
  if (_miTierKey === key) return;
  const sel = document.getElementById('miTier');
  if (!sel) return;
  const p = new URLSearchParams();
  if (df) p.set('date_from', df); if (dt2) p.set('date_to', dt2);
  let d;
  try { d = await api(`${API}/api/reports/menu-impact/tiers?${p}`); }
  catch (e) { return; }                      // keep whatever is in the box
  _miTierKey = key;
  const cur = sel.value;
  sel.innerHTML = '<option value="all">ทุกระดับราคา</option>' +
    (d.tiers || []).map(t => `<option value="${esc(t.tier)}">${
      /^\d+$/.test(t.tier) ? 'Tier ' + esc(t.label) : esc(t.label)}</option>`).join('');
  sel.value = [...sel.options].some(o => o.value === cur) ? cur : 'all';
}

async function loadMenuImpact(force) {
  loadPriceTier(force);   // tier section shares the same range/scope pickers
  const body = document.getElementById('miBody');
  if (!_miEnts) {
    try { _miEnts = await api(`${API}/api/reports/menu-impact/entities`); }
    catch (e) { body.innerHTML = '<p class="text-center text-rose-500 py-10">โหลดรายการเมนูไม่สำเร็จ</p>'; return; }
    if (!_miEnts.ready) {
      body.innerHTML = `<p class="text-center text-slate-400 py-10">${esc(_miEnts.hint || 'ยังไม่มีข้อมูล')}</p>`;
      _miEnts = null; return;
    }
    miFilterOptions();
    const sel = document.getElementById('miEnt');
    if (!sel.value && sel.options.length) sel.selectedIndex = 0;
    const [lo, hi] = _miEnts.coverage || [null, null];
    const fEl = document.getElementById('miFrom'), tEl = document.getElementById('miTo');
    // Open on the live menu, not on the whole coverage: a range starting before
    // the Grand Menu changeover blends two price lists (the server cuts it for
    // the tier analysis anyway). The pickers still reach the full coverage.
    const def0 = _miEnts.default_from || lo;
    if (lo && !fEl.value) { fEl.value = def0; fEl.min = lo; tEl.min = lo; }
    if (hi && !tEl.value) { tEl.value = hi; fEl.max = hi; tEl.max = hi; }
    document.getElementById('miCoverage').textContent =
      `ข้อมูล ${lo} → ${hi} · อัปเดต ${_miEnts.last_build || '-'}`
      + (def0 !== lo ? ` · เริ่มที่ ${def0} (เมนูใหม่)` : '');
  }
  const ent = document.getElementById('miEnt').value;
  if (!ent) { body.innerHTML = '<p class="text-center text-slate-400 py-10">เลือกวัตถุดิบ/เมนูก่อน</p>'; return; }
  const df = document.getElementById('miFrom').value, dt2 = document.getElementById('miTo').value;
  await miFillTiers(df, dt2);   // prices on sale in THIS window, nothing retired
  const scope = document.getElementById('miScope').value;
  const tier = document.getElementById('miTier').value;
  const key = `${ent}|${df}|${dt2}|${scope}|${tier}`;
  if (!force && _tabLastKey.menuimpact === key) return;
  body.innerHTML = '<p class="text-center text-slate-400 py-10">กำลังวิเคราะห์…</p>';
  const p = new URLSearchParams({ ent, scope, tier });
  if (df) p.set('date_from', df); if (dt2) p.set('date_to', dt2);
  let d;
  try { d = await api(`${API}/api/reports/menu-impact?${p}`); }
  catch (e) { body.innerHTML = '<p class="text-center text-rose-500 py-10">วิเคราะห์ไม่สำเร็จ</p>'; return; }
  _tabLastKey.menuimpact = key;
  if (!d.ready || d.error) {
    body.innerHTML = `<p class="text-center text-slate-400 py-10">${esc(d.error || d.hint || 'ไม่มีข้อมูล')}</p>`; return;
  }
  const n0 = v => Number(v || 0).toLocaleString('en-US');
  const b1 = v => '฿' + Number(v || 0).toLocaleString('en-US', { maximumFractionDigits: 1 });
  const sg = v => (v > 0 ? '+' : '') + Number(v || 0).toLocaleString('en-US', { maximumFractionDigits: 1 });
  const RED = '#b0472d', GRN = '#1f7a4d';
  const S = d.summary, C = d.compare;
  const kpis = [
    { l: 'สั่งใน % ของบิล', v: (S.pen_bills * 100).toFixed(1) + '%', sub: n0(S.bills_with) + ' บิล / ' + n0(d.totals.bills) + ' บิล', c: '#0e7490' },
    { l: 'ปริมาณ/หัว (เฉพาะบิลที่สั่ง)', v: Number(S.qty_head_takers || 0).toFixed(2), sub: 'รวม ' + n0(S.qty) + ' หน่วย', c: '#7a66b8' },
    { l: 'ต้นทุนของตัวมันเอง/หัว (บิลที่สั่ง)', v: b1(S.ecogs_head_takers), sub: 'รวม ' + b1(S.ecogs) + ' = ' + ((S.ecogs_share || 0) * 100).toFixed(1) + '% ของ COGS ทั้งหมด', c: RED },
    { l: 'Δ GP/หัว (มี − ไม่มี)', v: sg(C.gp.diff), sub: 'GP มี ' + b1(C.gp.with) + ' vs ไม่มี ' + b1(C.gp.without), c: C.gp.diff >= 0 ? GRN : RED },
  ].map(k => `<div class="card p-4 mi-kpi" style="border-top:3px solid ${k.c}"><p class="mi-kpi-label">${k.l}</p><p class="mi-kpi-value" style="color:${textInk(k.c)}">${k.v}</p><p class="mi-kpi-sub">${k.sub}</p></div>`).join('');
  const cmpRow = (l, o, inv) => {
    const col = (inv ? o.diff < 0 : o.diff > 0) ? GRN : RED;
    return `<tr class="border-t border-slate-100"><td class="py-1.5 pr-2">${l}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${b1(o.with)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${b1(o.without)}</td>
      <td class="py-1.5 text-right tabular-nums font-bold" style="color:${textInk(col)}">${sg(o.diff)}</td></tr>`;
  };
  const mixLine = C.hc_rel_saving == null ? '' :
    `<p class="text-[11px] mt-2" style="color:var(--text-muted)">ปรับสัดส่วนตามการกินรวม (mix-adjusted): การกินเมนูแพงควรอยู่ที่ ${b1(C.hc_expected_prop)}/หัว
     จริง ${b1(C.hc_other.with)} → <b style="color:${textInk(C.hc_rel_saving < 0 ? GRN : RED)}">${C.hc_rel_saving < 0 ? 'ช่วยเบรกเมนูแพง ' : 'ไม่ช่วยเบรก (กินเพิ่ม) '}${sg(C.hc_rel_saving)}/หัว</b></p>`;
  const verdict = (() => {
    const own = S.ecogs_head_takers || 0, rel = C.hc_rel_saving;
    let t = `<b>${esc(d.label)}</b> เพิ่มต้นทุน ${b1(own)}/หัวในบิลที่สั่ง (${((S.pen_bills || 0) * 100).toFixed(1)}% ของบิล)`;
    if (rel != null) t += rel < -1 ? ` แต่ช่วยลดการกินเมนูแพงเชิงสัดส่วน ${b1(-rel)}/หัว` : rel > 1 ? ` และมากับการกินเมนูแพงเพิ่ม ${b1(rel)}/หัว` : ' ผลต่อเมนูแพงเป็นกลาง';
    t += C.gp.diff >= 0 ? ` · บิลที่สั่งให้ GP/หัวสูงกว่า ${b1(C.gp.diff)} — เชิงกำไรถือว่ารับได้` : ` · บิลที่สั่งให้ GP/หัวต่ำกว่า ${b1(-C.gp.diff)} — ควรทบทวนพอร์ชัน/ต้นทุน`;
    return t;
  })();
  const trm = d.trend_month || [];
  // Δ GP/หัว far outside the pack = the tier where this item behaves differently
  // from everywhere else. That is the row to look at first, so paint it.
  const tGp = (d.tiers || []).map(t => t.d_gp);
  const tOut = _miOutlierSet(tGp);
  const tGpMax = Math.max(1, ...tGp.map(v => Math.abs(v || 0)));
  const tierRows = (d.tiers || []).map((t, i) => {
    const bad = tOut.has(i) && (t.d_gp || 0) < 0, good = tOut.has(i) && (t.d_gp || 0) > 0;
    const flag = bad ? '<span class="mi-flag bad" title="ผิดปกติเทียบ tier อื่น — GP/หัวต่างจากกลุ่มมาก">⚠ ผิดปกติ</span>'
               : good ? '<span class="mi-flag good" title="ผิดปกติเทียบ tier อื่น — GP/หัวดีกว่ากลุ่มมาก">★ เด่น</span>' : '';
    return `<tr class="border-t border-slate-100${bad ? ' mi-outlier' : good ? ' mi-outlier-good' : ''}">
    <td class="py-1 pr-2 font-semibold mi-name" data-sort="${esc(String(t.tier))}">Tier ${esc(_miTierLabel(t.tier))}${flag}</td>
    <td class="py-1 pr-2 text-right tabular-nums">${n0(t.bills)}</td>
    <td class="py-1 pr-2 text-right tabular-nums">${(t.pen * 100).toFixed(1)}%</td>
    <td class="py-1 pr-2 text-right tabular-nums">${t.qty_head_takers ?? '-'}</td>
    <td class="py-1 pr-2 text-right tabular-nums">${b1(t.ecogs_head_takers)}</td>
    <td class="py-1 pr-2 text-right tabular-nums font-semibold" style="color:${textInk((t.d_other_cogs || 0) < 0 ? GRN : RED)}">${t.d_other_cogs == null ? '-' : sg(t.d_other_cogs)}</td>
    <td class="py-1 text-right tabular-nums font-semibold" style="color:${textInk((t.d_gp || 0) >= 0 ? GRN : RED)}">${t.d_gp == null ? '-' : sg(t.d_gp)}${_miBar(t.d_gp, tGpMax, (t.d_gp || 0) >= 0 ? GRN : RED)}</td></tr>`;
  }).join('');
  const eraNote = d.era_tiers_only
    ? ` · แสดงเฉพาะ tier ของเมนูใหม่ (ตั้งแต่ ${d.menu_era_from}) — ราคาเก่าถูกยกเลิกแล้ว จึงไม่นำมารวม`
    : '';
  const tierCard = (d.tiers || []).length < 2 ? '' : `
    <div class="card p-5 overflow-x-auto mb-5">
      <h2 class="section-title mb-2">เจาะตามระดับราคาบุฟเฟ่ต์ — ใครสั่ง/กินเยอะ และผลต่อต้นทุน-กำไร ${uiQ('mi:tier', 'Tier ราคาบุฟเฟ่ต์', `<p>Tier = ราคาบุฟเฟ่ต์ผู้ใหญ่หน้าเมนูของช่วงนั้น อ่านจากบรรทัดแพ็กเกจในบิลจริง (ราคา NET รวม service charge 5% + VAT 7% แล้ว) · Δ คำนวณเทียบมี/ไม่มี ภายใน Tier เดียวกัน (คุมสาขา×วัน)${eraNote}</p>`)}</h2>
      <table class="text-xs w-full" id="miTierTbl"><thead><tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">
        <th class="py-1.5 pr-2">ระดับราคา</th><th class="py-1.5 pr-2 text-right">บิลในระดับราคา</th><th class="py-1.5 pr-2 text-right">% สั่ง</th>
        <th class="py-1.5 pr-2 text-right">ชิ้น/หัว (บิลที่สั่ง)</th><th class="py-1.5 pr-2 text-right">ต้นทุนตัวเอง/หัว</th>
        <th class="py-1.5 pr-2 text-right">Δ ต้นทุนเมนูอื่น/หัว</th><th class="py-1.5 text-right">Δ GP/หัว</th></tr></thead>
      <tbody>${tierRows}</tbody></table>
    </div>`;
  const brD = (d.branches || []).map(b => b.d_other_cogs);
  const brOut = _miOutlierSet(brD);
  const brMax = Math.max(1, ...brD.map(v => Math.abs(v || 0)));
  const brRows = (d.branches || []).map((b, i) => {
    const bad = brOut.has(i) && (b.d_other_cogs || 0) > 0, good = brOut.has(i) && (b.d_other_cogs || 0) < 0;
    const flag = bad ? '<span class="mi-flag bad" title="ผิดปกติเทียบสาขาอื่น — บิลที่สั่งกินเมนูอื่นแพงขึ้นผิดปกติ">⚠ ผิดปกติ</span>'
               : good ? '<span class="mi-flag good" title="ผิดปกติเทียบสาขาอื่น — ช่วยเบรกต้นทุนเมนูอื่นได้มากกว่าเพื่อน">★ เด่น</span>' : '';
    return `<tr class="border-t border-slate-100${bad ? ' mi-outlier' : good ? ' mi-outlier-good' : ''}">
    <td class="py-1 pr-2 font-semibold mi-name" data-sort="${esc(b.name)}">${esc(b.name)}${flag}</td>
    <td class="py-1 pr-2 text-right tabular-nums">${n0(b.bills)}</td>
    <td class="py-1 pr-2 text-right tabular-nums">${n0(b.qty)}</td>
    <td class="py-1 pr-2 text-right tabular-nums">${b.qty_head_takers ?? '-'}</td>
    <td class="py-1 pr-2 text-right tabular-nums">${b1(b.ecogs)}</td>
    <td class="py-1 text-right tabular-nums font-semibold" style="color:${textInk((b.d_other_cogs || 0) < 0 ? GRN : RED)}">${b.d_other_cogs == null ? '-' : sg(b.d_other_cogs)}${_miBar(b.d_other_cogs, brMax, (b.d_other_cogs || 0) < 0 ? GRN : RED)}</td></tr>`;
  }).join('');
  body.innerHTML = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">${kpis}</div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
      <div class="card p-5">
        <h2 class="section-title mb-1">เทียบต่อหัว: บิลที่มี vs ไม่มี (คุมสาขา×วัน)</h2>
        <p class="text-[11px] mb-2" style="color:var(--text-muted)">${esc(d.label)} · ${fmtDateTh(d.date_from)} – ${fmtDateTh(d.date_to)} · ${esc(d.scope_label)}${d.kind === 'group' ? ' · ' + (d.members || []).length + ' เมนูในกลุ่ม' : ' · ต้นทุน ฿' + d.unit_cost + '/หน่วย'}</p>
        <table class="text-xs w-full"><thead><tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">
          <th class="py-1.5 pr-2">฿/หัว</th><th class="py-1.5 pr-2 text-right">บิลที่มี</th><th class="py-1.5 pr-2 text-right">บิลที่ไม่มี</th><th class="py-1.5 text-right">ต่าง</th></tr></thead><tbody>
          ${cmpRow('ต้นทุนรวมทั้งบิล', C.cogs, true)}
          ${cmpRow('ต้นทุนเมนูอื่น (ไม่รวมตัวมันเอง)', C.other_cogs, true)}
          ${cmpRow('การกินเมนูแพง (≥฿30) ไม่รวมตัวมันเอง', C.hc_other, true)}
          ${cmpRow('รายได้ (ex-VAT)', C.rev, false)}
          ${cmpRow('กำไรขั้นต้น (GP)', C.gp, false)}
        </tbody></table>
        ${mixLine}
        <div class="mt-3 p-3 rounded text-xs mi-verdict" style="background:var(--bg-subtle);border:1px solid ${C.gp.diff >= 0 ? GRN : RED}">${verdict}</div>
      </div>
      <div class="card p-5">
        <h2 class="section-title mb-3">แนวโน้มรายเดือน — ปริมาณ/หัว และ % บิลที่สั่ง</h2>
        <p class="text-[11px] mb-2" style="color:var(--text-muted)">คลิกปุ่มขยายมุมกราฟเพื่อเจาะลึก + อ่านสรุป · จุดสีแดงคือเดือนที่ผิดปกติ</p>
        <div style="height:280px"><canvas id="miChart"></canvas></div>
      </div>
    </div>
    ${tierCard}
    <div class="card p-5 overflow-x-auto">
      <h2 class="section-title mb-3">รายสาขา — ใครขาย/ใครกินเยอะ และผลต่อต้นทุนเมนูอื่น ${uiQ('mi:branch', 'อ่านตารางรายสาขา', `<p>Δ ต้นทุนเมนูอื่น/หัว = บิลที่มีรายการนี้ กินเมนูอื่นแพงขึ้น/ถูกลงเท่าไร (คุมรายวันภายในสาขา) · เขียว = ช่วยลด · ⚠ = ค่าหลุดกรอบเทียบสาขาอื่น (Tukey fence) · คลิกหัวคอลัมน์เพื่อเรียงมาก→น้อย / น้อย→มาก</p>`)}</h2>
      <table class="text-xs w-full" id="miBranchTbl"><thead><tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">
        <th class="py-1.5 pr-2">สาขา</th><th class="py-1.5 pr-2 text-right">บิลที่สั่ง</th><th class="py-1.5 pr-2 text-right">จำนวน</th>
        <th class="py-1.5 pr-2 text-right">ชิ้น/หัว (บิลที่สั่ง)</th><th class="py-1.5 pr-2 text-right">ต้นทุนรวม</th>
        <th class="py-1.5 text-right">Δ ต้นทุนเมนูอื่น/หัว</th></tr></thead><tbody>${brRows}</tbody></table>
    </div>`;
  ['miTierTbl', 'miBranchTbl'].forEach(makeSortable);
  _miLast = d;
  if (trm.length) {
    // Outlier months get their own point colour so the anomaly is visible on
    // the chart itself, not only in the dive-in panel.
    const qh = trm.map(r => r.qty_head);
    const qOut = _miOutlierSet(qh);
    // mkChart (not `new Chart`) registers the instance, themes it for dark mode
    // and injects the dive-in button.
    _miChart = mkChart('miChart', { type: 'bar', data: {
        labels: trm.map(r => r.ym),
        datasets: [
          { type: 'bar', label: 'ปริมาณ/หัว', data: qh, yAxisID: 'y',
            backgroundColor: trm.map((r, i) => qOut.has(i) ? 'rgba(220,38,38,.75)' : 'rgba(31,122,77,.55)'),
            borderColor: trm.map((r, i) => qOut.has(i) ? '#dc2626' : 'transparent'),
            borderWidth: trm.map((r, i) => qOut.has(i) ? 1.5 : 0) },
          { type: 'line', label: '% บิลที่สั่ง', data: trm.map(r => r.pen == null ? null : r.pen * 100), borderColor: '#b0472d', backgroundColor: '#b0472d', tension: .25, yAxisID: 'y1' }]},
      options: { responsive: true, maintainAspectRatio: false,
        scales: { y: { title: { display: true, text: 'ชิ้น/หัว' } },
                  y1: { position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: '% บิล' } } } } });
  } else if (_miChart) { _miChart.destroy(); _miChart = null; }
}

// ── วิเคราะห์ Tier ราคาขาย (section of the tab) ───────────────────────
let _ptChartMix = null, _ptChartCogs = null, _ptSeq = 0;
// Granularity of each trend card, independently switchable: the mix chart is
// often read monthly while the cost chart is read weekly, so one shared toggle
// would force a compromise on whichever card you are not looking at.
let _ptGran = { mix: 'month', cogs: 'month' };
// Top-45 consumption unit. Per-head is the stored figure but reads as 0.147;
// per-100-covers is the same number a kitchen can act on (14.7 pieces per 100
// guests), so both are offered and the choice sticks across redraws.
let _ptQtyPer100 = false;
async function loadPriceTier(force) {
  const body = document.getElementById('ptBody');
  if (!body) return;
  const df = document.getElementById('miFrom').value, dt2 = document.getElementById('miTo').value;
  const scope = document.getElementById('miScope').value;
  const key = `${df}|${dt2}|${scope}`;
  if (!force && _tabLastKey.pricetier === key) return;
  const seq = ++_ptSeq;   // drop stale responses when loads overlap
  body.innerHTML = '<p class="text-center text-slate-400 py-10">กำลังวิเคราะห์ Tier…</p>';
  const p = new URLSearchParams({ scope });
  if (df) p.set('date_from', df); if (dt2) p.set('date_to', dt2);
  let d;
  try { d = await api(`${API}/api/reports/price-tier?${p}`); }
  catch (e) { if (seq === _ptSeq) body.innerHTML = '<p class="text-center text-rose-500 py-10">วิเคราะห์ Tier ไม่สำเร็จ</p>'; return; }
  if (seq !== _ptSeq) return;
  _tabLastKey.pricetier = key;
  if (!d.ready || d.error) {
    body.innerHTML = `<p class="text-center text-slate-400 py-10">${esc(d.error || d.hint || 'ไม่มีข้อมูล')}</p>`;
    document.getElementById('ptRange').textContent = ''; return;
  }
  _ptRender(d);
}

// Everything the tab draws from one payload. Split out of loadPriceTier so the
// granularity and per-100 toggles can redraw from _ptLast instead of re-running
// an endpoint that scans the fact tables to change a label.
function _ptRender(d) {
  const body = document.getElementById('ptBody');
  if (!body) return;
  const n0 = v => Number(v || 0).toLocaleString('en-US');
  const b1 = v => v == null ? '-' : '฿' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 1 });
  const sg = (v, suf = '') => v == null ? '-' : (v > 0 ? '+' : '') + Number(v).toLocaleString('en-US', { maximumFractionDigits: 1 }) + suf;
  const RED = '#b0472d', GRN = '#1f7a4d';
  const dc = (v, inv) => v == null ? 'var(--text-muted)' : (inv ? v < 0 : v > 0) ? GRN : RED;
  const PAL = ['#b0472d', '#0e7490', '#7a66b8', '#1f7a4d', '#d97706', '#64748b', '#be185d'];
  const tcolor = {}; d.tier_keys.forEach((t, i) => tcolor[t] = PAL[i % PAL.length]);
  const modeLbl = d.mode === 'prev' ? 'ช่วงก่อนหน้า' : 'ครึ่งแรกของช่วง (ประวัติไม่พอ)';
  // The window is cut at the Grand Menu changeover when it straddles it — say so,
  // otherwise the range in the header wouldn't match the one in the pickers.
  const eraCut = d.era_clamped ? ` · ตัดข้อมูลก่อน ${d.menu_era_from} ออก (ราคาเก่าเลิกขายแล้ว)` : '';
  document.getElementById('ptRange').textContent = (d.comparison_available === false
    ? `A: ${d.a_range[0]} → ${d.a_range[1]} · ${d.scope_label} · ยังไม่เทียบช่วงก่อนหน้า (คนละเมนูกัน)`
    : `A: ${d.a_range[0]} → ${d.a_range[1]} · เทียบ B (${modeLbl}): ${d.b_range[0]} → ${d.b_range[1]} · ${d.scope_label}`) + eraCut;

  const B = d.blend;
  const kpi = (l, v, dv, sub, inv) => `<div class="card p-4 mi-kpi" style="border-top:3px solid ${dc(dv, inv)}">
    <p class="mi-kpi-label">${l}</p>
    <p class="mi-kpi-value" style="color:var(--text-primary)">${v}</p>
    <p class="mi-kpi-sub font-semibold" style="color:${textInk(dc(dv, inv))}">${sub}</p></div>`;
  const dd = (a, b) => (a != null && b != null) ? a - b : null;
  const kpis =
    kpi('จำนวนคนทาน (ช่วง A)', n0(B.pax_a), B.d_pax_pct, `${sg(B.d_pax_pct, '%')} เทียบ B (${B.pax_b == null ? '-' : n0(B.pax_b)} คน)`, false) +
    kpi('รายได้/หัว (ex-VAT)', b1(B.rev_head_a), dd(B.rev_head_a, B.rev_head_b), `B: ${b1(B.rev_head_b)} · Δ ${sg(dd(B.rev_head_a, B.rev_head_b))}`, false) +
    kpi('ต้นทุนอาหาร/หัวบุฟเฟ่ต์', b1(B.cogs_head_a), dd(B.cogs_head_a, B.cogs_head_b), `B: ${b1(B.cogs_head_b)} · Δ ${sg(dd(B.cogs_head_a, B.cogs_head_b))}`, true) +
    kpi('ต้นทุนอาหาร %', (B.food_pct_a ?? '-') + '%', dd(B.food_pct_a, B.food_pct_b), `B: ${B.food_pct_b ?? '-'}% · Δ ${sg(dd(B.food_pct_a, B.food_pct_b), ' pp')}`, true);

  // Two things earn a flag on this table: a food cost the business itself calls
  // a problem (the 40% bar the action card uses), and a tier whose cost/head
  // moved far more than the others did (Tukey fence over the Δ column).
  const FOOD_PCT_BAR = 40;
  // COGS% is a FLOOR: a dish with no BoM cost books ฿0, so every uncosted plate
  // makes its tier look cheaper than it eats. Say so on the number itself —
  // ops reads its own food cost off requisitions and a silent 10-point gap is
  // exactly how the two sides end up arguing about which report is broken.
  const COV_BAR = 98;
  const covNote = x => (x.cost_coverage_pct == null || x.cost_coverage_pct >= COV_BAR) ? ''
    : `<span class="mi-flag warn" title="${n0(x.uncosted_plates)} จาน (${(100 - x.cost_coverage_pct).toFixed(1)}% ของจานที่เสิร์ฟใน tier นี้) ยังไม่มีต้นทุนในระบบ จึงคิดเป็น ฿0 — ตัวเลขนี้จึงเป็นค่า &quot;อย่างน้อย&quot; ไม่ใช่ต้นทุนจริง${x.est_food_pct != null ? ' · ถ้าคิดจานที่ยังไม่มีต้นทุนด้วยค่าเฉลี่ยของ tier นี้ จะอยู่ราว ' + x.est_food_pct + '%' : ''}">≥ ${x.est_food_pct != null ? '~' + x.est_food_pct + '%' : 'ยังไม่ครบ'}</span>`;
  const dCogsOut = _miOutlierSet(d.tiers.map(x => x.d_cogs_head));
  const tierRows = d.tiers.map((x, i) => {
    const hotFood = x.a.food_pct != null && x.a.food_pct >= FOOD_PCT_BAR;
    const jumped = dCogsOut.has(i) && (x.d_cogs_head || 0) > 0;
    const saved = dCogsOut.has(i) && (x.d_cogs_head || 0) < 0;
    const flags =
      (hotFood ? `<span class="mi-flag warn" title="COGS ≥ ${FOOD_PCT_BAR}% ของรายได้ — เกินเกณฑ์ที่ใช้ตั้งข้อเสนอแนะ">${x.a.food_pct}%</span>` : '') +
      (jumped ? '<span class="mi-flag bad" title="ต้นทุน/หัวขยับผิดปกติเทียบ tier อื่น">⚠ ผิดปกติ</span>' : '') +
      (saved ? '<span class="mi-flag good" title="ต้นทุน/หัวลดผิดปกติเทียบ tier อื่น (ในทางที่ดี)">★ เด่น</span>' : '');
    return `<tr class="border-t border-slate-100${jumped || hotFood ? ' mi-outlier' : saved ? ' mi-outlier-good' : ''}">
    <td class="py-1.5 pr-2 font-bold mi-name" data-sort="${esc(String(x.tier))}" style="color:${textInk(tcolor[String(x.tier)] || 'var(--text-primary)')}">${esc(x.label)}${flags}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${n0(x.a.pax)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${x.b ? n0(x.b.pax) : '-'}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums font-bold" style="color:${textInk(dc(x.d_pax_pct))}">${sg(x.d_pax_pct, '%')}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${x.a.share ?? '-'}%<span class="text-[10px]" style="color:${textInk(dc(x.d_share_pp))}"> (${sg(x.d_share_pp)})</span></td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${x.a.pax_bill ?? '-'}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${b1(x.a.rev_head)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${b1(x.a.cogs_head)}<span class="text-[10px] font-semibold" style="color:${textInk(dc(x.d_cogs_head, true))}"> (${sg(x.d_cogs_head)})</span></td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${x.bom_avg_cost != null ? b1(x.bom_avg_cost) : '-'}${x.bom_dish_count ? `<span class="text-[10px]" style="color:var(--text-muted)"> (${x.bom_dish_count} จาน)</span>` : ''}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${b1(x.a.hc_head)}<span class="text-[10px] font-semibold" style="color:${textInk(dc(x.d_hc_head, true))}"> (${sg(x.d_hc_head)})</span></td>
    <td class="py-1.5 pr-2 text-right tabular-nums font-semibold">${b1(x.a.gp_head)}</td>
    <td class="py-1.5 text-right tabular-nums${hotFood ? ' font-bold' : ''}" style="color:${textInk(hotFood ? RED : 'inherit')}">${x.a.food_pct ?? '-'}%${covNote(x)}</td></tr>`;
  }).join('');

  const insights = (d.insights || []).map(t => `<li style="margin:.3em 0">${esc(t)}</li>`).join('') || '<li>—</li>';
  const acts = (d.actions || []).map((a, i) => `<li style="margin:.45em 0"><b>${i + 1}. ${esc(a.title)}</b><br><span style="color:var(--text-secondary)">${esc(a.detail)}</span></li>`).join('');

  const tl = t => (d.tier_labels || {})[t] || t;
  // per-head → per-100-covers is a pure ×100 on the same stored figure; Δ% is a
  // ratio and therefore identical under either unit.
  const qUnit = _ptQtyPer100 ? 'ชิ้น/100 คน' : 'ชิ้น/หัว';
  const qv = v => v == null ? null : (_ptQtyPer100 ? Math.round(v * 100 * 10) / 10 : v);
  const tierTh = d.tier_keys.map(t => `<th class="py-1.5 pr-2 text-right" style="color:${textInk(tcolor[t])}">${esc(tl(t))}<br>${qUnit} (Δ%)</th>`).join('');
  // The menu name is frozen (sticky) while the tier columns scroll — with 5-8
  // tiers this table gets very wide, and losing track of which dish a column
  // belongs to made it unreadable.
  const STICKY_NUM_W = 22, STICKY_LBL_W = 190;
  const stickyTh = (left, w, extra = '') => `position:sticky;left:${left}px;width:${w}px;min-width:${w}px;background:var(--bg-card);z-index:2;${extra}`;
  // A dish whose per-head consumption jumped far more than the rest of the
  // Top-N is what actually moved the tier's cost — flag it rather than leaving
  // the reader to scan 45 rows of Δ%.
  const topOut = _miOutlierSet((d.top30 || []).map(r => r.d_qty_head_pct));
  const topRows = (d.top30 || []).map((r, i) => {
    const cells = d.tier_keys.map(t => {
      const c = r.tiers[t] || {};
      return `<td class="py-1 pr-2 text-right tabular-nums" data-sort="${c.qty_head ?? ''}">${qv(c.qty_head) ?? '-'}<span class="text-[10px] font-semibold" style="color:${textInk(dc(c.d_pct, true))}"> ${c.d_pct == null ? '' : '(' + sg(c.d_pct, '%') + ')'}</span></td>`;
    }).join('');
    const up = topOut.has(i) && (r.d_qty_head_pct || 0) > 0, down = topOut.has(i) && (r.d_qty_head_pct || 0) < 0;
    const flag = up ? '<span class="mi-flag bad" title="การทาน/หัวพุ่งผิดปกติเทียบเมนูอื่นในตารางนี้">⚠ พุ่ง</span>'
               : down ? '<span class="mi-flag good" title="การทาน/หัวลดผิดปกติเทียบเมนูอื่น (ต้นทุนดีขึ้น)">▼ ลดแรง</span>' : '';
    return `<tr class="border-t border-slate-100${up ? ' mi-outlier' : down ? ' mi-outlier-good' : ''}">
      <td class="py-1 pr-1 text-slate-400 rank-cell" style="${stickyTh(0, STICKY_NUM_W)}">${i + 1}</td>
      <td class="py-1 pr-2 font-semibold mi-name" data-sort="${esc(r.label)}" style="${stickyTh(STICKY_NUM_W, STICKY_LBL_W, 'box-shadow:2px 0 3px -2px rgba(0,0,0,.15)')}">${esc(r.label)}${flag}</td>
      <td class="py-1 pr-2 text-right tabular-nums">${b1(r.unit_cost)}</td>
      <td class="py-1 pr-2 text-right tabular-nums font-semibold">${b1(r.cogs_head)}</td>
      <td class="py-1 pr-2 text-right tabular-nums">${qv(r.qty_head) ?? '-'}<span class="text-[10px] font-semibold" style="color:${textInk(dc(r.d_qty_head_pct, true))}"> (${sg(r.d_qty_head_pct, '%')})</span></td>
      ${cells}</tr>`;
  }).join('');

  // Per-dish BoM cost for each tier — CUMULATIVE: a ฿1,299 guest also eats
  // every ฿459 dish, so the server folds the cheaper tiers in and sorts the
  // whole list richest-cost-first. The average hides which dishes drive it.
  // A tier's list is therefore a superset of the tier below it.
  const bomDetailTiers = d.tiers.filter(x => (x.bom_dishes || []).length > 0);
  const bomDetailSections = bomDetailTiers.map((x, ti) => {
    // A dish costing more than double its own tier's average is the one
    // pulling that average up — flag it instead of leaving the reader to
    // eyeball 40-plus rows of raw numbers.
    const outlierAt = x.bom_avg_cost ? x.bom_avg_cost * 2 : null;
    const rows = x.bom_dishes.map((r, i) => {
      const isOutlier = outlierAt != null && r.bom_cost != null && r.bom_cost > outlierAt;
      return `<tr class="border-t border-slate-100${isOutlier ? ' mi-outlier' : ''}">
      <td class="py-1 pr-1 text-slate-400 rank-cell">${i + 1}</td>
      <td class="py-1 pr-2 mi-name" data-sort="${esc(r.label)}">${esc(r.label)}${isOutlier ? ' <span class="mi-flag bad" title="ต้นทุนเกิน 2 เท่าของค่าเฉลี่ย tier นี้">แพงผิดปกติ</span>' : ''}</td>
      <td class="py-1 pr-2 text-[10px]" style="color:var(--text-muted)">${esc(r.sku_code)}</td>
      <td class="py-1 pr-2 text-right tabular-nums font-semibold"${isOutlier ? ' style="color:var(--bad-ink)"' : ''}>${b1(r.bom_cost)}</td></tr>`;
    }).join('');
    return `<details class="mb-2"${ti === 0 ? ' open' : ''}>
      <summary class="cursor-pointer text-xs font-bold py-1" style="color:${textInk(tcolor[String(x.tier)] || 'var(--text-primary)')}">
        ${esc(x.label)} — กินได้ ${x.bom_dishes.length} จาน (เฉลี่ย ${b1(x.bom_avg_cost)}) · รวมจาน tier ที่ถูกกว่าแล้ว
      </summary>
      <table class="text-xs w-full mt-1 pt-bom-tbl"><thead><tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">
        <th class="py-1 pr-1" data-nosort>#</th><th class="py-1 pr-2">เมนู</th><th class="py-1 pr-2">รหัส</th><th class="py-1 pr-2 text-right">ต้นทุน BoM</th>
      </tr></thead><tbody>${rows}</tbody></table>
      <p class="text-[10px] mt-1" style="color:var(--text-muted)">= ต้นทุนเกิน 2 เท่าของค่าเฉลี่ย tier นี้</p>
    </details>`;
  }).join('');

  body.innerHTML = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">${kpis}</div>
    <div class="card p-5 mb-5 overflow-x-auto">
      <h2 class="section-title mb-3">ระดับราคา — คนทานและต้นทุน/หัว (A เทียบ B) ${uiQ('pt:tier-table', 'อ่านตารางระดับราคา', `<p>"เมนูแพง" = เมนูต้นทุน ≥ ฿30/หน่วย · <b>COGS% เป็นค่าต่ำสุด</b> — จานที่ยังไม่มีต้นทุนในระบบ (เครื่องดื่ม/น้ำจิ้ม/เมนูที่ยังไม่ได้คีย์สูตร) ถูกคิดเป็น ฿0 ป้าย <span class="mi-flag warn">~%</span> คือค่าประมาณเมื่อคิดจานเหล่านั้นด้วยราคาเฉลี่ยของ tier นั้น · ต้นทุน/หัว มาจาก<b>บิลจริง</b> (ทุกจานที่ลูกค้าในบิลนั้นกิน ไม่ใช่เฉพาะเมนูของ tier) · GP/หัว = รายได้ − ต้นทุนอาหาร (ยังไม่หักค่าแรง/ค่าเช่า) · โปรฯ/ราคาอื่น = บิลที่ราคาบุฟเฟ่ต์ไม่ตรงราคามาตรฐาน (ส่วนลด/โปรโมชัน) · เด็กล้วน/อื่นๆ = บิลไม่มีบุฟเฟ่ต์ผู้ใหญ่ · "ต้นทุน BoM เฉลี่ย/จาน" = ต้นทุนสูตรเฉลี่ยของ<b>ทุกจานที่ tier นั้นกินได้</b> (สะสม — รวมจานของ tier ที่ถูกกว่าด้วย เพราะลูกค้า ฿1,299 กินจาน ฿459 ได้) จากสูตรอาหาร ไม่ใช่ยอดขายจริง — มีเฉพาะเมนูใหม่ตั้งแต่ 1 ก.ย. 2569 · <b>คลิกหัวคอลัมน์เพื่อเรียง</b> · = COGS ≥ 40% ของรายได้ · ⚠ = ต้นทุน/หัวขยับหลุดกรอบเทียบ tier อื่น</p><p class="mt-2 p-2 pt-promo-note"><b class="k">“โปรฯ/ราคาอื่น” ไม่ใช่ tier ราคา</b> — เป็นถังรวมของบิลที่ราคาบุฟเฟ่ต์ผู้ใหญ่ <b>ต่อหัว</b> ไม่ตรงรายการราคาที่ขายจริงในวันนั้น (ยอมคลาดได้ ±฿3 บนราคาก่อน SC+VAT ถึง 31 ส.ค. 2569 · ±฿4 บนราคา NET ของ Grand Menu ตั้งแต่ 1 ก.ย. 2569) เกิดได้ 3 ทาง: <b>ลดทั้งบิล</b> (ทุกหัวราคาเดียวกันแต่นอกเมนู เช่น ฿710 = ลดจาก ฿789) · <b>มีหัวฟรี/ครึ่งราคาปน</b> กับหัวเต็มราคา ทำให้ค่าเฉลี่ยต่อหัวตกอยู่ระหว่าง tier ซึ่ง<b>ไม่ใช่ราคาที่ตั้งขาย</b> · <b>ฟรีทั้งใบ</b> (ยอดผู้ใหญ่รวม = ฿0) · <b>วิธีอ่าน:</b> รายได้/หัว ของแถวนี้ต่ำกว่า tier ที่ลูกค้าทานจริง แต่ต้นทุน/หัว ยังเป็นของที่กินจริง → <b>COGS% สูงผิดตาเป็นเรื่องปกติ</b> ไม่ใช่สัญญาณว่าต้นทุนพุ่ง · ถ้าสัดส่วนคนทานแถวนี้โตผิดปกติ ให้ไปดูการอนุมัติส่วนลด ไม่ใช่ครัว · แยก 3 แบบ + รายสาขา ดูได้ในรายงานผู้บริหาร §11</p>`)}</h2>
      <table class="text-xs w-full" id="ptTierTbl"><thead>
        <tr class="text-center" style="color:var(--text-muted)">
          <th></th>
          <th colspan="5" class="py-1 border-b-2" style="background:rgba(31,58,107,.07);border-color:rgba(31,58,107,.35)">ลูกค้า</th>
          <th colspan="6" class="py-1 border-b-2" style="background:rgba(176,71,45,.07);border-color:rgba(176,71,45,.35)">฿ ต้นทุนและกำไร/หัว</th>
        </tr>
        <tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">
        <th class="py-1.5 pr-2">ระดับราคา</th><th class="py-1.5 pr-2 text-right">คนทาน A</th><th class="py-1.5 pr-2 text-right">คนทาน B</th>
        <th class="py-1.5 pr-2 text-right">Δ คนทาน</th><th class="py-1.5 pr-2 text-right">สัดส่วน (Δpp)</th>
        <th class="py-1.5 pr-2 text-right">คน/บิล</th><th class="py-1.5 pr-2 text-right">รายได้/หัว</th>
        <th class="py-1.5 pr-2 text-right" title="นับเฉพาะบิลบุฟเฟ่ต์ (หาร pax บุฟเฟ่ต์) — ไม่ใช่ต้นทุน/หัวทั้งเครือในหน้า ต้นทุน/หัว ซึ่งหารลูกค้าทุกบิลรวมเดลิเวอรี/กลับบ้าน">ต้นทุน/หัวบุฟเฟ่ต์ (Δ)</th><th class="py-1.5 pr-2 text-right">ต้นทุน BoM เฉลี่ย/จาน</th><th class="py-1.5 pr-2 text-right">เมนูแพง/หัว (Δ)</th>
        <th class="py-1.5 pr-2 text-right">GP/หัว</th><th class="py-1.5 text-right">ต้นทุนขาย %</th>
      </tr></thead><tbody>${tierRows}</tbody></table>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
      <div class="card p-5"><h2 class="section-title mb-3">สิ่งที่เห็นจากข้อมูล (A เทียบ B)</h2><ul class="text-xs" style="list-style:none;padding:0;margin:0">${insights}</ul></div>
      <div class="card p-5" style="border:1px solid var(--indigo,#4a70b0)"><h2 class="section-title mb-3">สิ่งที่ต้องปรับปรุงและพัฒนา</h2><ol class="text-xs" style="list-style:none;padding:0;margin:0">${acts}</ol></div>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
      <div class="card p-5">
        <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
          <h2 class="section-title mb-0">แนวโน้มสัดส่วนคนทานตามระดับราคา ${uiQ('pt:chart-mix', 'แนวโน้มสัดส่วนคนทาน', '<p>คลิกปุ่มมุมกราฟเพื่อเจาะลึก + อ่านสรุปการย้าย tier</p>')}</h2>${ptGranBtns('mix')}</div>
        <div style="height:280px"><canvas id="ptChartMix"></canvas></div>
        <p class="text-[10.5px] mt-2" id="ptNoteMix" style="color:var(--text-muted)"></p></div>
      <div class="card p-5">
        <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
          <h2 class="section-title mb-0">แนวโน้มต้นทุนอาหาร/หัว ตามระดับราคา ${uiQ('pt:chart-cogs', 'แนวโน้มต้นทุนอาหาร/หัว', '<p>คลิกปุ่มมุมกราฟเพื่อเจาะลึก · จุดใหญ่สีแดง = ช่วงที่ต้นทุน/หัวหลุดกรอบของ tier นั้น</p>')}</h2>${ptGranBtns('cogs')}</div>
        <div style="height:280px"><canvas id="ptChartCogs"></canvas></div>
        <p class="text-[10.5px] mt-2" id="ptNoteCogs" style="color:var(--text-muted)"></p></div>
    </div>
    <div class="card p-5 overflow-x-auto">
      <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
        <h2 class="section-title mb-0">Top-${(d.top30 || []).length} เมนูหลักในต้นทุน — การทานต่อหัวแยกตาม Tier (A, วงเล็บ = Δ% เทียบ B) ${uiQ('pt:top30', 'อ่านตาราง Top เมนู', `<p>เรียงตามต้นทุนรวมในช่วง A · ${qUnit} = จำนวนที่ทาน ÷ คนทานทั้งหมดของ tier นั้น${_ptQtyPer100 ? ' × 100' : ''} · เขียว = ทานลดลง (ต้นทุนดีขึ้น) · คอลัมน์ # และเมนู ตรึงไว้ขณะเลื่อนดู tier อื่น · <b>คลิกหัวคอลัมน์เพื่อเรียงมาก→น้อย / น้อย→มาก</b> (คลิกครั้งที่สามกลับลำดับเดิม) · ⚠ = การทาน/หัวหลุดกรอบเทียบเมนูอื่น</p>`)}</h2>
        <div class="flex gap-1 items-center">${ptQtyUnitBtns()}
        <button onclick="exportTable('ptTop30','price_tier_top30.csv')" class="text-xs border border-slate-200 rounded px-2 py-0.5" style="color:var(--text-muted)">CSV</button></div>
      </div>
      <table class="text-xs w-full" id="ptTop30"><thead><tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">
        <th class="py-1.5 pr-1" data-nosort style="${stickyTh(0, STICKY_NUM_W)}">#</th><th class="py-1.5 pr-2" style="${stickyTh(STICKY_NUM_W, STICKY_LBL_W)}">เมนู</th><th class="py-1.5 pr-2 text-right">ต้นทุน/หน่วย</th>
        <th class="py-1.5 pr-2 text-right" title="นับเฉพาะบิลบุฟเฟ่ต์ (หาร pax บุฟเฟ่ต์) — ไม่ใช่ต้นทุน/หัวทั้งเครือในหน้า ต้นทุน/หัว ซึ่งหารลูกค้าทุกบิลรวมเดลิเวอรี/กลับบ้าน">ต้นทุน/หัวบุฟเฟ่ต์</th><th class="py-1.5 pr-2 text-right">${qUnit} รวม (Δ%)</th>${tierTh}
      </tr></thead><tbody>${topRows}</tbody></table>
    </div>
    <div class="card p-5 overflow-x-auto">
      <h2 class="section-title mb-3">รายละเอียดต้นทุน BoM ต่อจานในแต่ละระดับราคา ${uiQ('pt:bom-detail', 'ต้นทุนต่อจานแต่ละระดับราคา', `<p>แต่ละ tier = <b>ทุกจานที่ลูกค้า tier นั้นกินได้</b> (สะสมจาก tier ที่ถูกกว่าขึ้นมา) เรียงต้นทุนสูตรจากแพงไปถูก ใช้ดูว่าจานไหนดันค่าเฉลี่ยขึ้น/ลง — ไม่ใช่ยอดขายจริง</p>`)}</h2>
      ${bomDetailSections || '<p class="text-xs" style="color:var(--text-muted)">ยังไม่มีข้อมูล BoM รายจาน — มีเฉพาะเมนูใหม่ตั้งแต่ 1 ก.ย. 2569</p>'}
    </div>`;

  _ptLast = d;
  ['ptTierTbl', 'ptTop30'].forEach(makeSortable);
  document.querySelectorAll('#ptBody .pt-bom-tbl').forEach(makeSortable);
  _ptDrawTrends(d, tcolor);
}

// Both trend cards, drawn at whatever granularity each one is toggled to.
// Split out of the renderer so the toggle can redraw a single card without
// re-fetching or rebuilding the whole tab.
function _ptDrawTrends(d, tcolor) {
  const wkMeta = d.trend_week_meta || {};
  const OLD = wkMeta.old_suffix || '@old';
  // Weekly carries tiers the A-window never had (the pre-Grand-Menu price list),
  // so its keys/labels come from the payload's own meta, not from tier_keys.
  const pick = (which) => {
    const weekly = _ptGran[which] === 'week';
    const rows = (weekly ? d.trend_week : d.trend_month) || [];
    return {
      weekly, rows,
      labels: rows.map(r => weekly ? r.wk.replace('-W', ' W') : r.ym),
      keys: weekly ? (wkMeta.tier_keys || []) : (d.tier_keys || []),
      label: k => weekly ? ((wkMeta.tier_labels || {})[k] || k)
                         : ((d.tier_labels || {})[k] || k),
      // an old-era series borrows its live twin's colour and is drawn faded +
      // dashed, so "฿999" and "฿999 (เมนูเก่า)" read as related but distinct.
      colour: k => tcolor[k.replace(OLD, '')] || '#94a3b8',
      isOld: k => k.endsWith(OLD),
    };
  };

  const M = pick('mix');
  if (M.rows.length) {
    const dsMix = M.keys.map(t => ({
      label: M.label(t),
      data: M.rows.map(r => r.tiers[t]?.share ?? 0),
      backgroundColor: M.isOld(t) ? M.colour(t) + '66' : M.colour(t), stack: 's' }));
    _ptChartMix = mkChart('ptChartMix', {
      type: 'bar', data: { labels: M.labels, datasets: dsMix },
      options: { responsive: true, maintainAspectRatio: false,
        scales: { x: { stacked: true }, y: { stacked: true, max: 100, title: { display: true, text: '% ของคนทาน' } } },
        plugins: {
          // 8 weeks x up to 14 bands: printing every share turns the bars into a
          // wall of digits. Label only the bands big enough to read.
          datalabels: M.weekly ? { display: c => (c.dataset.data[c.dataIndex] || 0) >= 8 } : {},
          tooltip: { callbacks: {
          label: c => `${c.dataset.label}: ${c.parsed.y}%`,
          afterBody: ci => _ptPartialTip(M.rows[ci[0].dataIndex]) } } } } });
  } else if (_ptChartMix) { _ptChartMix.destroy(); _ptChartMix = null; }

  const C = pick('cogs');
  if (C.rows.length) {
    const dsCogs = C.keys.filter(t => t.replace(OLD, '') !== 'child').map(t => {
      const series = C.rows.map(r => r.tiers[t]?.cogs_head ?? null);
      // A period whose cost/head breaks out of its own tier's range is the one
      // worth opening — make its point large and red instead of one more dot.
      const out = _miOutlierSet(series), col = C.colour(t);
      return { label: C.label(t), data: series,
               borderColor: col, backgroundColor: col, tension: .25, spanGaps: true,
               borderDash: C.isOld(t) ? [5, 3] : undefined,
               pointRadius: series.map((_, i) => out.has(i) ? 6 : 3),
               pointBackgroundColor: series.map((_, i) => out.has(i) ? '#dc2626' : col),
               pointBorderColor: series.map((_, i) => out.has(i) ? '#dc2626' : col) };
    });
    _ptChartCogs = mkChart('ptChartCogs', {
      type: 'line', data: { labels: C.labels, datasets: dsCogs },
      options: { responsive: true, maintainAspectRatio: false,
        scales: { y: { title: { display: true, text: '฿/หัว' } } },
        plugins: {
          // Weekly can carry both price lists at once — 14 lines x 8 points, so the
          // per-point values collide into noise. The tooltip still has every number.
          datalabels: C.weekly ? { display: false } : {},
          tooltip: { callbacks: {
          afterBody: ci => _ptPartialTip(C.rows[ci[0].dataIndex]) } } } } });
  } else if (_ptChartCogs) { _ptChartCogs.destroy(); _ptChartCogs = null; }

  _ptTrendNote('ptNoteMix', M, wkMeta);
  _ptTrendNote('ptNoteCogs', C, wkMeta);
}

// A short week is the single most misleading thing on these charts — the newest
// one is nearly always cut off by the sync running a day behind. Say so on hover.
function _ptPartialTip(row) {
  return (row && row.partial) ? `⚠ สัปดาห์ไม่เต็ม — มีข้อมูล ${row.days} วัน` : '';
}

function _ptTrendNote(id, P, wkMeta) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!P.weekly) { el.textContent = 'รายเดือน · เดือนล่าสุดอาจยังไม่ครบเดือน'; return; }
  const short = P.rows.filter(r => r.partial).map(r => r.wk.replace('-W', ' W'));
  el.innerHTML = `รายสัปดาห์ (จ.–อา. ตามมาตรฐาน ISO) ${P.rows.length} สัปดาห์ล่าสุด`
    + (wkMeta.era_week ? ` · เส้นประ/สีจาง = ราคาเมนูเก่า (ก่อน ${esc(wkMeta.era_from || '')}) `
        + `แยกเส้นไว้เพราะ ฿999 ของสองเมนูคนละราคากัน` : '')
    + (short.length ? ` · <b style="color:var(--amber-ink)">⚠ สัปดาห์ไม่เต็ม: ${short.map(esc).join(', ')}</b>` : '');
}

// Top-45's per-head ⇄ per-100-covers switch. Redraws the tab from the payload
// already in hand (_ptLast) — the unit is a presentation choice, not new data.
function ptQtyUnitBtns() {
  const b = (on, txt) => `<button onclick="ptSetQtyUnit(${on})" class="text-xs rounded px-2 py-0.5 border"
    style="${_ptQtyPer100 === on ? 'background:var(--bg-hover);font-weight:700;border-color:var(--accent)'
                                 : 'border-color:var(--border-soft);color:var(--text-muted)'}">${txt}</button>`;
  return `${b(false, 'ต่อหัว')}${b(true, 'ต่อ 100 คน')}`;
}

function ptSetQtyUnit(on) {
  if (_ptQtyPer100 === on || !_ptLast) return;
  _ptQtyPer100 = on;
  _ptRender(_ptLast);
}

// The toggle in each trend card header.
function ptGranBtns(which) {
  const b = (g, txt) => `<button onclick="ptSetGran('${which}','${g}')" class="text-xs rounded px-2 py-0.5 border"
    style="${_ptGran[which] === g ? 'background:var(--bg-hover);font-weight:700;border-color:var(--accent)'
                                  : 'border-color:var(--border-soft);color:var(--text-muted)'}">${txt}</button>`;
  return `<div class="flex gap-1" id="ptGran${which}">${b('month', 'รายเดือน')}${b('week', 'รายสัปดาห์')}</div>`;
}

function ptSetGran(which, g) {
  if (_ptGran[which] === g || !_ptLast) return;
  _ptGran[which] = g;
  // The payload already carries BOTH granularities, so this is a redraw, not a
  // reload — re-fetching the whole tier report to switch axis would be a wasted
  // round-trip on an endpoint that scans the fact tables.
  _ptRender(_ptLast);
}

// ── Dive-in summaries for the tab's three charts ──────────────────────
// The generic analyser (analyzeChartForDive) only sees a chart's datasets, so on
// these it could say "the peak is March" and nothing about the ingredient, the
// tier or the menu era. These read the payload the tab already has and write the
// summary in the report's own terms. Registered into the enhancer table from
// here (js/06-features.js loads first, so _DIVE_ENHANCERS already exists).
const _MI_D2 = v => v == null ? '-' : '฿' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 1 });
const _MI_SG = v => v == null ? '-' : (v > 0 ? '+' : '') + Number(v).toLocaleString('en-US', { maximumFractionDigits: 1 });

// The months whose value breaks out of the series' own range, as a bullet.
function _miOutlierBullet(labels, series, fmtV, what) {
  const out = _miOutlierSet(series);
  if (!out.size) return _diveBullet('✓', `ไม่พบเดือนที่${what}ผิดปกติ — ซีรีส์นี้เคลื่อนไหวอยู่ในกรอบเดียวกันตลอดช่วง`, 'good');
  const names = [...out].map(i => `<b>${esc(labels[i])}</b> (${fmtV(series[i])})`).join(', ');
  return _diveBullet('', `เดือนที่${what}หลุดกรอบ (Tukey fence): ${names} — เจาะดูเดือนเหล่านี้ก่อน`, 'warn');
}

async function _diveMenuImpact(_cfg) {
  const d = _miLast;
  if (!d) return '<p class="text-xs text-slate-400 p-2">ยังไม่มีข้อมูล — โหลดแท็บใหม่อีกครั้ง</p>';
  const t = d.trend_month || [];
  if (!t.length) return '<p class="text-xs text-slate-400 p-2">ไม่มีข้อมูลรายเดือนสำหรับรายการนี้</p>';
  const labels = t.map(r => r.ym);
  const qh = t.map(r => r.qty_head), pen = t.map(r => r.pen == null ? null : r.pen * 100);
  const first = qh.find(v => v != null), last = [...qh].reverse().find(v => v != null);
  const chg = (first && last != null) ? (last - first) / first * 100 : null;
  const peak = qh.indexOf(Math.max(...qh.filter(v => v != null)));
  const low = qh.indexOf(Math.min(...qh.filter(v => v != null)));
  const S = d.summary, C = d.compare;
  return _diveSection('รายการนี้คืออะไร', '',
      _diveMetric('วัตถุดิบ/เมนู', esc(d.label)) +
      _diveMetric('ช่วง', `${d.date_from} → ${d.date_to}`) +
      _diveMetric('ขอบเขต', esc(d.scope_label)) +
      _diveMetric('สั่งใน % ของบิล', ((S.pen_bills || 0) * 100).toFixed(1) + '%') +
      _diveMetric('ต้นทุนตัวเอง/หัว (บิลที่สั่ง)', _MI_D2(S.ecogs_head_takers), 'bad') +
      _diveMetric('Δ GP/หัว (มี − ไม่มี)', _MI_SG(C.gp.diff), C.gp.diff >= 0 ? 'good' : 'bad')) +
    _diveSection('อ่านกราฟนี้ยังไง', '▲',
      _diveBullet('', `แท่ง = <b>ปริมาณ/หัว</b> (ทานกี่ชิ้นต่อหัวลูกค้าทั้งหมด) · เส้น = <b>% บิลที่สั่ง</b> (ความนิยม) — สองเส้นนี้แยกกัน: ของที่คนสั่งน้อยแต่สั่งทีเยอะ จะเห็นแท่งสูงพร้อมเส้นต่ำ`) +
      (chg == null ? '' : _diveBullet(chg >= 0 ? '▲' : '▼',
        `ปริมาณ/หัว ${chg >= 0 ? 'เพิ่มขึ้น' : 'ลดลง'} <b>${Math.abs(chg).toFixed(1)}%</b> จากเดือนแรก (${first}) มาเดือนล่าสุด (${last}) ของช่วงนี้`,
        chg >= 0 ? 'warn' : 'good')) +
      _diveBullet('▲', `สูงสุด <b>${esc(labels[peak])}</b> (${qh[peak]} ชิ้น/หัว) · ต่ำสุด <b>${esc(labels[low])}</b> (${qh[low]} ชิ้น/หัว)`) +
      (pen.some(v => v != null) ? _diveBullet('', `% บิลที่สั่งล่าสุด <b>${(pen.filter(v => v != null).slice(-1)[0]).toFixed(1)}%</b> ของบิลทั้งหมดในเดือนนั้น`) : '') +
      _miOutlierBullet(labels, qh, v => v + ' ชิ้น/หัว', 'ปริมาณ/หัว')) +
    _diveSection('สรุปเชิงธุรกิจ', '',
      _diveBullet(C.gp.diff >= 0 ? '✓' : '⚠',
        `บิลที่สั่ง${esc(d.label)} ให้ GP/หัว ${C.gp.diff >= 0 ? 'สูงกว่า' : 'ต่ำกว่า'} บิลที่ไม่สั่ง <b>${_MI_D2(Math.abs(C.gp.diff))}</b> ` +
        `(${_MI_D2(C.gp.with)} vs ${_MI_D2(C.gp.without)}) — คุมความต่างของสาขา×วันแล้ว`,
        C.gp.diff >= 0 ? 'good' : 'bad') +
      (C.hc_rel_saving == null ? '' : _diveBullet(C.hc_rel_saving < 0 ? '✗' : '',
        C.hc_rel_saving < 0
          ? `ช่วย <b>เบรกการกินเมนูแพง</b> เชิงสัดส่วน ${_MI_D2(-C.hc_rel_saving)}/หัว — ของถูกที่ทำให้ทานของแพงน้อยลง`
          : `มากับการกินเมนูแพง <b>เพิ่ม</b> ${_MI_D2(C.hc_rel_saving)}/หัว — ทานคู่กับของแพง ไม่ได้ทดแทน`,
        C.hc_rel_saving < 0 ? 'good' : 'bad')));
}

async function _divePtMix(_cfg) {
  const d = _ptLast;
  if (!d || !(d.trend_month || []).length) return '<p class="text-xs text-slate-400 p-2">ยังไม่มีข้อมูลรายเดือน</p>';
  const t = d.trend_month, labels = t.map(r => r.ym), keys = d.tier_keys || [];
  const tl = k => (d.tier_labels || {})[k] || k;
  const lastM = t[t.length - 1], firstM = t[0];
  const shareNow = keys.map(k => ({ k, s: lastM.tiers[k]?.share ?? 0 })).sort((a, b) => b.s - a.s);
  const shifts = keys.map(k => ({ k, dv: (lastM.tiers[k]?.share ?? 0) - (firstM.tiers[k]?.share ?? 0) }))
                     .sort((a, b) => Math.abs(b.dv) - Math.abs(a.dv));
  const up = shifts.find(x => x.dv > 0), dn = shifts.find(x => x.dv < 0);
  return _diveSection('ช่วงที่กำลังดู', '',
      _diveMetric('A', `${d.a_range[0]} → ${d.a_range[1]}`) +
      _diveMetric('ขอบเขต', esc(d.scope_label)) +
      _diveMetric('จำนวน tier ที่ยังขาย', String(keys.length)) +
      _diveMetric('เดือนล่าสุดในกราฟ', esc(labels[labels.length - 1]))) +
    _diveSection('สัดส่วนคนทานตอนนี้', '',
      shareNow.slice(0, 5).map(x =>
        _diveMetric(esc(tl(x.k)), x.s.toFixed(1) + '%')).join('') +
      _diveBullet('1.', `tier ที่คนทานเยอะสุดเดือนล่าสุดคือ <b>${esc(tl(shareNow[0].k))}</b> ที่ ${shareNow[0].s.toFixed(1)}% ของคนทานทั้งหมด`)) +
    _diveSection('การย้าย tier ตลอดช่วง', '',
      (up ? _diveBullet('▲', `<b>${esc(tl(up.k))}</b> สัดส่วนเพิ่มมากสุด ${up.dv >= 0 ? '+' : ''}${up.dv.toFixed(1)} pp (${esc(labels[0])} → ${esc(labels[labels.length - 1])})`, 'good') : '') +
      (dn ? _diveBullet('▼', `<b>${esc(tl(dn.k))}</b> สัดส่วนลดมากสุด ${dn.dv.toFixed(1)} pp — ถ้าเป็น tier ราคาสูง แปลว่ารายได้/หัวถูกกดจาก mix ไม่ใช่จากราคา`, 'warn') : '') +
      (d.mix_effect == null ? _diveBullet('ℹ️', 'ยังไม่มีช่วงก่อนหน้าที่เทียบได้ จึงยังไม่คำนวณผลของ mix ต่อรายได้/หัว')
        : _diveBullet(d.mix_effect < 0 ? '⚠' : '✓',
          `ผลจาก mix อย่างเดียว ${d.mix_effect < 0 ? 'กด' : 'ดัน'}รายได้/หัว <b>${_MI_D2(Math.abs(d.mix_effect))}</b> เทียบช่วงก่อนหน้า`,
          d.mix_effect < 0 ? 'bad' : 'good')) +
      (d.era_clamped ? _diveBullet('', `กราฟเริ่มที่ <b>${d.menu_era_from}</b> — ราคาเมนูเก่าถูกยกเลิกไปแล้ว จึงไม่นำมารวมในสัดส่วน`, 'info') : ''));
}

async function _divePtCogs(_cfg) {
  const d = _ptLast;
  if (!d || !(d.trend_month || []).length) return '<p class="text-xs text-slate-400 p-2">ยังไม่มีข้อมูลรายเดือน</p>';
  const t = d.trend_month, labels = t.map(r => r.ym);
  const keys = (d.tier_keys || []).filter(k => k !== 'child');
  const tl = k => (d.tier_labels || {})[k] || k;
  const lastM = t[t.length - 1];
  const now = keys.map(k => ({ k, v: lastM.tiers[k]?.cogs_head })).filter(x => x.v != null)
                  .sort((a, b) => b.v - a.v);
  // Biggest month-on-month move across all tiers — the thing that moved the line.
  let move = null;
  keys.forEach(k => {
    const s = t.map(r => r.tiers[k]?.cogs_head ?? null);
    for (let i = 1; i < s.length; i++) {
      if (s[i] == null || s[i - 1] == null) continue;
      const dv = s[i] - s[i - 1];
      if (!move || Math.abs(dv) > Math.abs(move.dv)) move = { k, dv, from: labels[i - 1], to: labels[i] };
    }
  });
  const hot = (d.tiers || []).filter(x => x.a.food_pct != null && x.a.food_pct >= 40)
                             .sort((a, b) => b.a.food_pct - a.a.food_pct);
  // Outliers are judged per tier — a ฿1,299 tier costs more than a ฿459 one by
  // design, so pooling them would flag the price list, not an anomaly.
  const outBullets = keys.map(k => {
    const s = t.map(r => r.tiers[k]?.cogs_head ?? null);
    const o = _miOutlierSet(s);
    if (!o.size) return '';
    return _diveBullet('', `<b>${esc(tl(k))}</b> — เดือนที่ต้นทุน/หัวหลุดกรอบ: ` +
      [...o].map(i => `${esc(labels[i])} (${_MI_D2(s[i])})`).join(', '), 'warn');
  }).join('');
  return _diveSection('ช่วงที่กำลังดู', '',
      _diveMetric('A', `${d.a_range[0]} → ${d.a_range[1]}`) +
      _diveMetric('ต้นทุนอาหาร/หัวบุฟเฟ่ต์ (รวมทุก tier)', _MI_D2(d.blend.cogs_head_a), 'bad') +
      _diveMetric('ต้นทุนอาหาร %', (d.blend.food_pct_a ?? '-') + '%',
                  (d.blend.food_pct_a || 0) >= 40 ? 'bad' : 'good') +
      _diveMetric('รายได้/หัว', _MI_D2(d.blend.rev_head_a), 'good')) +
    _diveSection('ต้นทุน/หัว รายเดือนล่าสุด', '',
      now.map(x => _diveMetric(esc(tl(x.k)), _MI_D2(x.v))).join('') +
      (now.length > 1 ? _diveBullet('↔️',
        `ช่วงห่างระหว่าง tier แพงสุด (<b>${esc(tl(now[0].k))}</b> ${_MI_D2(now[0].v)}) กับถูกสุด ` +
        `(<b>${esc(tl(now[now.length - 1].k))}</b> ${_MI_D2(now[now.length - 1].v)}) = ` +
        `<b>${_MI_D2(now[0].v - now[now.length - 1].v)}</b>/หัว`) : '')) +
    _diveSection('สิ่งที่ต้องดู', '⚠',
      (move ? _diveBullet(move.dv > 0 ? '▲' : '▼',
        `ขยับแรงสุดคือ <b>${esc(tl(move.k))}</b> ${move.dv > 0 ? 'เพิ่ม' : 'ลด'} <b>${_MI_D2(Math.abs(move.dv))}</b>/หัว ` +
        `(${esc(move.from)} → ${esc(move.to)})`, move.dv > 0 ? 'bad' : 'good') : '') +
      (hot.length ? _diveBullet('',
        `tier ที่ COGS ≥ 40% ของรายได้: ${hot.map(x => `<b>${esc(x.label)}</b> (${x.a.food_pct}%)`).join(', ')} — ` +
        `นี่คือเกณฑ์เดียวกับที่การ์ด "สิ่งที่ต้องปรับปรุงและพัฒนา" ใช้ตั้งข้อเสนอแนะ`, 'bad')
        : _diveBullet('✓', 'ไม่มี tier ไหน COGS แตะ 40% ของรายได้ในช่วงนี้', 'good')) +
      (outBullets || _diveBullet('✓', 'ไม่พบเดือนที่ต้นทุน/หัวหลุดกรอบของ tier ตัวเอง', 'good')) +
      (d.era_clamped ? _diveBullet('', `เส้นเริ่มที่ <b>${d.menu_era_from}</b> — ต้นทุน/หัวของเมนูเก่าคนละชุดราคา จึงไม่ต่อเส้นกัน`, 'info') : ''));
}

_DIVE_ENHANCERS.miChart = _diveMenuImpact;
_DIVE_ENHANCERS.ptChartMix = _divePtMix;
_DIVE_ENHANCERS.ptChartCogs = _divePtCogs;

// DV5: on a phone in แตะง่าย the ranking is the page's first card list (the branch, its gap to the chain and the dishes that push it)
PAGE_CARDS.costheadtop60 = [{ sel: '#chtRankTable', title: 'สาขา', sub: ['เมนูที่ดันต้นทุนขึ้น'], keyCols: ['Δ vs ภาพรวม', 'ต้นทุน/หัว', 'หัวลูกค้า'], labels: ['Δ/หัว เทียบภาพรวม', 'ต้นทุน/หัว', 'หัวลูกค้า'] }];
async function loadCostHeadTop60(force) {
  const sel = document.getElementById('chtMonth');
  if (!_chtMonthsLoaded && sel) {
    try {
      const mi = await api(`${API}/api/months`);
      const months = (mi.months || mi || []).slice().sort().reverse();
      sel.innerHTML = months.map(m => `<option value="${m}">${m}</option>`).join('');
      _chtMonthsLoaded = true;
    } catch (e) {}
  }
  const scope = document.getElementById('chtScope')?.value || 'master';
  // PR-PP3: the month is the top bar's (one period control per page; this page is month-grain: เดือน only). The month in
  // progress reads the month table as it stands (no to-date cut on a month table; there is no period comparison here).
  const _gr = gPeriodResolve();
  const mv = _gr ? _gr.cur[1].slice(0, 7) : '';
  const keyMonth = mv || 'auto';
  if (!force && _tabLastKey.costheadtop60 === 'm:' + keyMonth + '|s:' + scope) return;
  const body = document.getElementById('chtBody');
  body.innerHTML = '<p class="text-center text-slate-400 py-10">กำลังโหลด…</p>';
  const p = new URLSearchParams(); if (mv) p.set('month', mv); p.set('scope', scope);
  let d;
  try { d = await api(`${API}/api/reports/cost-head-top60?${p}`); }
  catch (e) { body.innerHTML = '<p class="text-center text-rose-500 py-10">โหลดข้อมูลไม่สำเร็จ</p>'; return; }
  // export links + range label follow the resolved month + scope; snap the picker to it
  const m = d.month || mv;
  if (sel) { if (d.month) sel.value = d.month; sel.dataset.cbset = '1'; }
  _tabLastKey.costheadtop60 = 'm:' + (d.month || keyMonth) + '|s:' + scope;
  const sl = d.scope_label || 'Master';
  document.getElementById('chtPrint').href = `${API}/api/reports/cost-head-top60.print?month=${m}&scope=${scope}`;
  document.getElementById('chtXlsx').href  = `${API}/api/reports/cost-head-top60.xlsx?month=${m}&scope=${scope}`;
  document.getElementById('chtRange').textContent = m ? `${d.branches.length} สาขา ${sl}` : '';
  if (m) {
    const _end = `${m}-${String(_prMlen(+m.slice(0, 4), +m.slice(5, 7))).padStart(2, '0')}`;
    periodChip((_gr && _gr.cur[0].slice(0, 7) === m) ? _gr : { label_th: periodLabelTh(`${m}-01`, _end), complete: true }, true);
  }
  if (!d.overview) { body.innerHTML = `<p class="text-center text-slate-400 py-10">ไม่มีข้อมูลสำหรับเดือน ${m} (ขอบเขต ${sl})</p>`; return; }

  // ── formatting helpers ──
  const n0 = v => Number(v || 0).toLocaleString('en-US');
  const q2 = v => Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const b2 = v => '฿' + q2(v);
  const sgn = (v, dgt = 2) => (v > 0 ? '+' : v < 0 ? '−' : '') + q2(Math.abs(v));
  const RED = '#b0472d', GRN = '#1f7a4d';
  const dot = p => p ? '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#b0472d;margin-right:5px;vertical-align:middle"></span>' : '';
  const ov = d.overview, A = d.analysis || {};

  // ── KPI hero (overview) ──
  const kpis = [
    { l: 'จำนวนหัวลูกค้า (รวม)', v: n0(ov.covers), sub: d.branches.length + ' สาขา ' + sl, c: '#0e7490' },
    { l: 'ต้นทุนอาหาร/หัว เฉลี่ย', v: b2(ov.cph_total), sub: 'ฐานเทียบของทุกสาขา', c: RED },
    { l: 'ต้นทุนอาหาร %', v: ov.food_pct.toFixed(1) + '%', sub: 'COGS ÷ ยอดขาย ex-VAT', c: '#7a66b8' },
    { l: 'ต้นทุนอาหารรวม', v: fmt(ov.total_cogs), sub: ov.menu_count + ' เมนูมีต้นทุน', c: '#0d9488' },
  ].map(k => `<div class="card p-4" style="border-top:3px solid ${k.c}"><p class="text-[11px] uppercase tracking-wide" style="color:var(--text-muted)">${k.l}</p><p class="text-2xl font-bold" style="color:${textInk(k.c)}">${k.v}</p><p class="text-[11px]" style="color:var(--text-muted)">${k.sub}</p></div>`).join('');

  // ── cross-branch ranking ──
  const rank = (A.ranked || []).map(b => {
    const gc = b.gap > 0 ? RED : GRN;
    return `<tr class="border-t border-slate-100">
      <td class="py-1.5 pr-2 font-semibold">${esc(b.name)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${n0(b.covers)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-bold">${b2(b.cph_total)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${textInk(gc)}">${sgn(b.gap)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${b.food_pct.toFixed(1)}%</td>
      <td class="py-1.5 text-[11px]" style="color:var(--text-muted)">${esc(b.drivers || '')}</td></tr>`;
  }).join('');
  const rankTbl = `<div class="card p-5 mb-4 overflow-x-auto">
    <h2 class="section-title mb-3">จัดอันดับสาขา — ต้นทุน/หัว เทียบภาพรวม</h2>
    <table class="text-xs w-full" id="chtRankTable"><thead><tr class="text-left border-b-2 border-slate-300" style="color:var(--text-muted)">
      <th class="py-1.5 pr-2">สาขา</th><th class="py-1.5 pr-2 text-right">หัวลูกค้า</th>
      <th class="py-1.5 pr-2 text-right">ต้นทุน/หัว</th><th class="py-1.5 pr-2 text-right">Δ vs ภาพรวม</th>
      <th class="py-1.5 pr-2 text-right">ต้นทุนอาหาร %</th><th class="py-1.5">เมนูที่ดันต้นทุนขึ้น (บาท/หัว)</th>
    </tr></thead><tbody>${rank}
      <tr class="border-t-2 border-slate-300" style="background:var(--bg-subtle)">
        <td class="py-1.5 pr-2 font-bold">ภาพรวม (ฐานเทียบ)</td>
        <td class="py-1.5 pr-2 text-right tabular-nums font-bold">${n0(ov.covers)}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums font-bold">${b2(ov.cph_total)}</td>
        <td class="py-1.5 pr-2 text-right">—</td>
        <td class="py-1.5 pr-2 text-right tabular-nums font-bold">${ov.food_pct.toFixed(1)}%</td><td></td></tr>
    </tbody></table></div>`;

  // ── headline callout ──
  let headline = '';
  if (A.hottest) {
    const drv = (A.hottest_drivers || []).map(x => `${esc(x.desc)} (+${q2(x.d)}/หัว)`).join(', ');
    headline = `<div class="card p-4 mb-4" style="border:1px solid ${RED};background:#fdf3f0">
      <p class="text-sm font-bold" style="color:${textInk(RED)}">สาขาต้นทุน/หัวสูงสุด: ${esc(A.hottest)} (${b2(d.branches.find(b=>b.name===A.hottest)?.cph_total||0)}/หัว · ${sgn(A.hottest_gap)} จากภาพรวม)</p>
      <p class="text-xs mt-1" style="color:var(--text-secondary)">ตัวขับหลัก: ${drv}. ส่วนต่าง ${sgn(A.hottest_gap)} บาท/หัว × ${n0(A.hottest_covers)} หัว ≈ <b>${fmt(A.hottest_excess_month)}</b> ที่ใช้ต้นทุนต่างจากค่าเฉลี่ยกลุ่มเดือนนี้ ${uiQ('cht:hottest', 'สาขาต้นทุน/หัวสูงสุด', '<p>ส่วนต่างที่ใช้ต้นทุนต่างจากค่าเฉลี่ยกลุ่มเดือนนี้ — เป้าตรวจสอบพอร์ชั่น/ของเสีย/การ map รหัสลำดับแรก.</p>')}</p></div>`;
  }

  // ── menu table builders ──
  // DV5: the chain's Top 60 shows its first 10 rows (with สะสม% the reader sees where to stop); the rest behind "ดูทั้งหมด"
  const ovN = ov.menus.length;
  const ovRows = ov.menus.map((r, i) => `<tr${dvMoreAttr('chtOvTable', i, ovN, 10)}>
    <td class="num text-slate-400">${r.rank}</td>
    <td>${dot(r.prem)}${esc(r.desc)} <span class="text-slate-400 text-[10px]">[${esc(r.code)}]</span></td>
    <td class="num">${n0(r.qty)}</td><td class="num">${q2(r.qph)}</td>
    <td class="num">${n0(r.cogs)}</td><td class="num font-semibold">${b2(r.cph)}</td>
    <td class="num">${r.share.toFixed(1)}%</td><td class="num text-slate-400">${r.cum.toFixed(1)}%</td></tr>`).join('');
  const ovTable = `<div class="card p-5 mb-4">
    <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
      <h2 class="section-title mb-0">ภาพรวมทุกสาขา — Top 60 ต้นทุน/หัว</h2>
      <button onclick="exportTable('chtOvTable','top60_costhead_overview_${m}.csv')" class="text-xs border border-slate-200 rounded px-2 py-0.5" style="color:var(--text-muted)">CSV</button></div>
    <div class="overflow-auto" style="max-height:560px"><table class="tbl-top60 w-full" id="chtOvTable"><thead><tr>
      <th class="num">#</th><th>เมนู</th><th class="num">จำนวน</th><th class="num">/หัว</th>
      <th class="num">ต้นทุนรวม</th><th class="num">ต้นทุน/หัว</th><th class="num">% ต้นทุน</th><th class="num">สะสม%</th>
    </tr></thead><tbody>${ovRows}</tbody></table></div>${dvMoreBtn('chtOvTable', ovN, 10)}</div>`;

  // ── per-branch sections ──
  const branchHtml = d.branches.map(b => {
    const gc = b.gap > 0 ? RED : GRN;
    const li = (x, col) => `<li style="margin:.15em 0">${dot(x.prem)}<b>${esc(x.desc)}</b> <span style="color:${textInk(col)};font-weight:600">${sgn(x.d)}/หัว</span> <span class="text-slate-400 text-[10px]">(${b2(x.b_cph)} vs ${b2(x.o_cph)})</span></li>`;
    const hi = (b.highs || []).slice(0, 5).map(x => li(x, RED)).join('') || '<li class="text-slate-400">—</li>';
    const lo = (b.lows || []).slice(0, 5).map(x => li(x, GRN)).join('') || '<li class="text-slate-400">—</li>';
    const rows = b.menus.map(r => {
      const dc = r.d > 0 ? RED : r.d < 0 ? GRN : 'var(--text-muted)';
      return `<tr>
        <td class="num text-slate-400">${r.rank}</td>
        <td>${dot(r.prem)}${esc(r.desc)} <span class="text-slate-400 text-[10px]">[${esc(r.code)}]</span></td>
        <td class="num">${n0(r.qty)}</td><td class="num">${q2(r.qph)}</td>
        <td class="num">${n0(r.cogs)}</td><td class="num font-semibold">${b2(r.cph)}</td>
        <td class="num text-slate-400">${r.o_cph != null ? b2(r.o_cph) : '—'}</td>
        <td class="num" style="color:${textInk(dc)};font-weight:600">${sgn(r.d)}</td>
        <td class="num" style="color:${textInk(dc)}">${r.dp != null ? (r.dp > 0 ? '+' : '') + r.dp + '%' : 'ใหม่'}</td></tr>`;
    }).join('');
    return `<div class="card p-5 mb-4">
      <div class="flex items-center justify-between flex-wrap gap-2 mb-1">
        <h2 class="section-title mb-0">${esc(b.name)}</h2>
        <span class="text-xs tabular-nums" style="color:var(--text-muted)">หัวลูกค้า ${n0(b.covers)} · ต้นทุน/หัว <b style="color:${textInk(gc)}">${b2(b.cph_total)}</b> · Δ <b style="color:${textInk(gc)}">${sgn(b.gap)}</b> · Food ${b.food_pct.toFixed(1)}%</span>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 my-2 text-xs">
        <div><p class="font-semibold mb-1" style="color:${textInk(RED)}">▲ ดันต้นทุนสูงกว่าภาพรวม</p><ul style="list-style:none;padding:0;margin:0">${hi}</ul></div>
        <div><p class="font-semibold mb-1" style="color:${textInk(GRN)}">▼ ต่ำกว่าภาพรวม</p><ul style="list-style:none;padding:0;margin:0">${lo}</ul></div>
      </div>
      <details class="mt-1"><summary class="cursor-pointer text-xs font-semibold select-none" style="color:var(--text-secondary)">ดูตาราง Top 60 ทั้งหมด (${b.menu_count} เมนูมีต้นทุน) · <button onclick="event.preventDefault();exportTable('chtBr${b.branch_id}','top60_${esc(b.name)}_${m}.csv')" class="underline">CSV</button></summary>
        <div class="overflow-auto mt-2" style="max-height:520px"><table class="tbl-top60 w-full" id="chtBr${b.branch_id}"><thead><tr>
          <th class="num">#</th><th>เมนู</th><th class="num">จำนวน</th><th class="num">/หัว</th>
          <th class="num">ต้นทุนรวม</th><th class="num">ต้นทุน/หัว</th><th class="num">ภาพรวม/หัว</th><th class="num">Δ/หัว</th><th class="num">Δ%</th>
        </tr></thead><tbody>${rows}</tbody></table></div></details></div>`;
  }).join('');

  // ── action plan (static guidance) ──
  const action = `<div class="card p-5 mb-4">
    <h2 class="section-title mb-3">แนวทาง ตรวจสอบ · พัฒนา · ติดตามแก้ไข ${uiQ('cht:action', 'แนวทางตรวจสอบ', '<p>① ตรวจสอบ: โฟกัสเมนูจุดแดงราคาแพงที่ Δ/หัวบวกมากก่อน — ดึงบิลขาดทุน/ส่วนลด/Void มาดูว่าเป็นการกินจริง/ของเสีย/แจกฟรี/map รหัสผิด</p><p class="mt-2">เกณฑ์ “มีสาระสำคัญ”: Δ/หัวต่างจากภาพรวมชัดและเป็นบาท/หัวที่มีนัย (เน้นวัตถุดิบแพง). ตัวเลขจากฐานข้อมูลจริง — “สาเหตุ” เป็นสมมุติฐานเพื่อตรวจสอบ ไม่ใช่ข้อสรุปความผิด.</p>')}</h2>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs" style="color:var(--text-secondary)">
      <div style="border:1px solid ${RED};padding-left:10px">
        <p class="font-bold mb-1" style="color:var(--text-primary)">① ตรวจสอบ</p>
        <ul style="padding-left:1em;margin:0"><li>โฟกัสเมนูจุดแดงราคาแพงที่ Δ/หัวบวกมากก่อน — ดึงบิลขาดทุน/ส่วนลด/Void มาดู</li><li>Yield test: ชั่ง/นับพอร์ชั่นจริงเทียบ BoM (แซลมอน/หอยเชลล์/กุ้ง/ทุเรียน)</li><li>ตรวจ sku_cost เทียบราคาซื้อจริง โดยเฉพาะเมนูที่ขึ้น “ใหม่”</li></ul></div>
      <div style="border:1px solid #0d9488;padding-left:10px">
        <p class="font-bold mb-1" style="color:var(--text-primary)">② พัฒนา</p>
        <ul style="padding-left:1em;margin:0"><li>มาตรฐานพอร์ชั่นเป็นภาพ + เครื่องชั่งประจำสเตชั่นทะเล</li><li>คุมการเติม (refill) วัตถุดิบแพงในสาขาที่สูงผิดปกติ</li><li>Menu engineering + จัดซื้อ/สเปกรวมศูนย์ให้ทุกสาขาเท่ากัน</li></ul></div>
      <div style="border:1px solid var(--accent);padding-left:10px">
        <p class="font-bold mb-1" style="color:var(--text-primary)">③ ติดตามแก้ไข</p>
        <ul style="padding-left:1em;margin:0"><li>ตั้งเป้าต้นทุน/หัวรายสาขา = ภาพรวม ${b2(ov.cph_total)} หรือดีกว่า</li><li>ทบทวนรายเดือนด้วยรายงานนี้ (เปลี่ยนเดือนด้านบน) ดูว่า Δ/หัวเมนูเป้าหมายลดลงไหม</li><li>มอบเจ้าของงานต่อสาขา + วัดซ้ำ 30 วัน (เกณฑ์ผ่าน Δ/หัวลด ≥ 50%)</li></ul></div>
    </div></div>`;

  // ── charts row (canvases filled after innerHTML lands in the DOM) ──
  const chartsRow = `<div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card p-4"><h3 class="section-title mb-2" style="font-size:13px">ต้นทุน/หัว รายสาขา เทียบค่าเฉลี่ย</h3>
      <div style="height:300px"><canvas id="chtChartBranch"></canvas></div></div>
    <div class="card p-4"><h3 class="section-title mb-2" style="font-size:13px">12 อันดับเมนูตัวขับต้นทุน/หัว (ภาพรวม)</h3>
      <div style="height:300px"><canvas id="chtChartMenus"></canvas></div></div>
  </div>`;

  // DV5 (phone table review 2026-10-25): the answer first - the branch furthest above the chain and the ranking with "เมนูที่ดันต้นทุนขึ้น",
  // then the figures, the charts and the Top 60 lists
  body.innerHTML = headline + rankTbl + `<div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">${kpis}</div>` + chartsRow + ovTable + branchHtml + action;
  const rk0 = (A.ranked || []).filter(b => b.gap > 0);
  const exc = b => b.gap * (b.covers || 0);
  pageAnswer('costheadtop60', [
    A.hottest && ansLine('ต้นทุน/หัวสูงกว่าภาพรวมมากสุด', `${A.hottest} ${dvBaht(A.hottest_excess_month)}`,
      `${sgn(A.hottest_gap)} ฿/หัว × ${n0(A.hottest_covers)} หัว${(A.hottest_drivers || [])[0] ? ' · ' + A.hottest_drivers[0].desc + ' +฿' + q2(A.hottest_drivers[0].d) + '/หัว' : ''}`, 'bad', '#chtRankTable'),
    rk0.filter(b => b.name !== A.hottest).sort((a, b) => exc(b) - exc(a)).slice(0, 1).map(b => ansLine('อันดับถัดไป', `${b.name} ${dvBaht(exc(b))}`,
      `${sgn(b.gap)} ฿/หัว × ${n0(b.covers)} หัว`, 'bad', '#chtRankTable'))[0],
    ansLine('ต้นทุนอาหาร/หัว เฉลี่ย', `${b2(ov.cph_total)} · ${ov.food_pct.toFixed(1)}%`, `${n0(ov.covers)} หัว · ${d.branches.length} สาขา ${sl}`, '', '#chtOvTable'),
  ]);

  // Chart 1 — branch cost/head bars (red=above overview, green=below) + overview average line
  const rk = A.ranked || [];
  mkChart('chtChartBranch', {
    type: 'bar',
    data: {
      labels: rk.map(b => b.name),
      datasets: [
        { label: 'ต้นทุน/หัว', data: rk.map(b => b.cph_total),
          backgroundColor: rk.map(b => b.gap > 0 ? RED : GRN), borderRadius: 3, order: 2 },
        { label: 'ค่าเฉลี่ยภาพรวม', type: 'line', data: rk.map(() => ov.cph_total),
          borderColor: '#475569', borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, order: 1 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, labels: { boxWidth: 12, font: { size: 10 } } },
        datalabels: { display: false },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ฿${q2(c.parsed.y)}` } },
      },
      scales: { y: { ticks: { callback: v => '฿' + v } }, x: { ticks: { font: { size: 10 } } } },
    },
  });

  // Chart 2 — Top-12 menus by cost/head (premium = red, else teal), horizontal
  const top12 = ov.menus.slice(0, 12);
  mkChart('chtChartMenus', {
    type: 'bar',
    data: {
      labels: top12.map(mn => mn.desc.length > 28 ? mn.desc.slice(0, 27) + '…' : mn.desc),
      datasets: [{ label: 'ต้นทุน/หัว', data: top12.map(mn => mn.cph),
        backgroundColor: top12.map(mn => mn.prem ? RED : '#0d9488'), borderRadius: 3 }],
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: c => `฿${q2(c.parsed.x)}/หัว` } },
      },
      scales: { x: { ticks: { callback: v => '฿' + v } }, y: { ticks: { font: { size: 9.5 } } } },
    },
  });
}

// ══════════ EXTRA (POS เชิงลึก) ══════════
async function _extraMonths(selId, valNow) {
  const sel = document.getElementById(selId);
  if (!sel || sel.dataset.loaded) return;
  try {
    const mi = await api(`${API}/api/months`);
    const months = ((mi.months || mi) || []).slice().sort().reverse();
    const keep = sel.querySelector('option[value=""]');
    sel.innerHTML = (keep ? keep.outerHTML : '') + months.map(m => `<option value="${m}">${m}</option>`).join('');
    sel.dataset.loaded = '1';
  } catch (e) {}
}
const _extKpi = (l, v, sub, c) => `<div class="card p-4" style="border-top:3px solid ${c}"><p class="text-[11px] uppercase tracking-wide" style="color:var(--text-muted)">${l}</p><p class="text-2xl font-bold" style="color:${textInk(c)}">${v}</p><p class="text-[11px]" style="color:var(--text-muted)">${sub||''}</p></div>`;

// Make a LOCAL picker follow the GLOBAL period. These endpoints take a single
// month/day, so the picker snaps to the global END month/day whenever the global
// range changes (a manual local pick still wins until the global changes again).
// Since 2026-09 a tab declares which picker follows in TABS (`follow: {...}`,
// js/01-core.js) and loadActiveTab calls these before the loader — no loader
// calls them directly any more.
const _extraLastGlobal = {};
function _syncExtraMonth(selId, tabKey) {
  const sel = document.getElementById(selId); if (!sel) return;
  const gm = getMonthTo() || getMonthFrom() || '';
  if (gm && gm !== _extraLastGlobal[tabKey] && [...sel.options].some(o => o.value === gm)) {
    sel.value = gm;
  }
  _extraLastGlobal[tabKey] = gm;
}
// Date variants of the same idea — snap a LOCAL single-date / date-range picker to
// the GLOBAL period whenever the global filter changes (manual local picks still win
// until the global changes again). Used by the day-grain report/drill tabs.
function _lastDayOfMonth(ym) {
  const [y, m] = ym.split('-').map(Number);
  return `${ym}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`;  // day 0 of next month
}
function _globalEndDate() {
  const g = (typeof currentGranularity !== 'undefined') ? currentGranularity : 'monthly';
  if (g === 'daily' || g === 'weekly') {
    const dt = document.getElementById('selDateTo')?.value;
    const df = document.getElementById('selDateFrom')?.value;
    if (dt) return dt;
    if (df) return df;
  }
  const m = getMonthTo() || getMonthFrom() || '';
  if (!m) return '';                              // "latest"/"all" → leave the tab's own default
  // The latest month is still in progress, so its calendar month-end is a FUTURE,
  // data-less day. Leave the tab on its own latest-day default for that case; only
  // snap to month-end for a past, completed month.
  const months = Array.isArray(window.allMonths) ? window.allMonths : [];
  if (months.length && m === months[months.length - 1]) return '';
  return _lastDayOfMonth(m);
}
function _syncExtraDate(inputId, tabKey) {
  const el = document.getElementById(inputId); if (!el) return;
  const gd = _globalEndDate();
  // Only act on a real GLOBAL change (so a manual local pick survives a same-filter
  // reload). gd='' = "latest/all" → clear the input so the tab falls back to its own
  // latest-day default; a concrete date snaps the tab to it.
  if (gd !== _extraLastGlobal[tabKey]) el.value = gd;
  _extraLastGlobal[tabKey] = gd;
}
function _syncExtraDateRange(fromId, toId, tabKey) {
  const sf = document.getElementById(fromId), st = document.getElementById(toId);
  if (!sf || !st) return;
  const g = (typeof currentGranularity !== 'undefined') ? currentGranularity : 'monthly';
  let from = '', to = '';
  if (g === 'daily' || g === 'weekly') {
    from = document.getElementById('selDateFrom')?.value || '';
    to   = document.getElementById('selDateTo')?.value   || '';
  } else {
    const mf = getMonthFrom(), mt = getMonthTo() || getMonthFrom();
    if (mf) from = mf + '-01';
    if (mt) to   = _lastDayOfMonth(mt);
  }
  const key = from + '|' + to;
  if (key !== _extraLastGlobal[tabKey]) {
    if (from) sf.value = from;
    if (to)   st.value = to;
  }
  _extraLastGlobal[tabKey] = key;
}
function _syncExtraMonthRange(fromId, toId, tabKey) {
  const sf = document.getElementById(fromId), st = document.getElementById(toId);
  if (!sf || !st) return;
  const gf = getMonthFrom() || '', gt = getMonthTo() || getMonthFrom() || '';
  const key = gf + '|' + gt;
  if (key !== _extraLastGlobal[tabKey]) {
    if (gf && [...sf.options].some(o => o.value === gf)) sf.value = gf;
    if (gt && [...st.options].some(o => o.value === gt)) st.value = gt;
  }
  _extraLastGlobal[tabKey] = key;
}
// PR-PP1 · a page's periodBar (registered under `barKey`) follows the top bar's วัน / สัปดาห์ / เดือน / ปี (getPeriodKind).
// Only a real global change moves it, and only to a kind the page offers (a month-only cost page keeps เดือน on สัปดาห์).
function _syncExtraPeriod(barKey, tabKey) {
  const bar = _pbars[barKey]; if (!bar) return;
  const gk = getPeriodKind(), k = 'period|' + tabKey;
  if (gk !== _extraLastGlobal[k] && bar.kinds.includes(gk) && bar.get().kind !== gk) bar.set({ kind: gk }, true);
  _extraLastGlobal[k] = gk;
}

// เงินสดขาด/เกิน
async function loadCashCtl(force) {
  await _extraMonths('ccMonth');
  periodHeadline('phd-cashctl', 'cashctl');   // PR-PP2: the top bar's month
  const mv = document.getElementById('ccMonth')?.value || '';
  const _fk = _filterKey() + '|' + mv;
  if (!force && _tabLastKey.cashctl === _fk) return; _tabLastKey.cashctl = _fk;
  const p = new URLSearchParams(); if (mv) p.set('month', mv);
  if (getBranch()) p.set('branch', getBranch()); if (getGroup()) p.set('group', getGroup());
  let d; try { d = await api(`${API}/api/extra/cash?${p}`); } catch (e) { return; }
  if (d.month) { const el = document.getElementById('ccMonth'); if (el && !el.value) el.value = d.month; }
  document.getElementById('ccRange').textContent = d.month ? `เดือน ${d.month}` : '';
  const s = d.summary || {};
  document.getElementById('ccKpis').innerHTML = [
    _extKpi('ขาด/เกิน สุทธิ', fmt(s.net || 0), 'ทั้งเครือเดือนนี้', (s.net || 0) < 0 ? '#ef4444' : '#10b981'),
    _extKpi('ยอดขาดรวม', fmt(s.short_total || 0), (s.short_days || 0) + ' วัน-สาขา ที่ขาด', '#ef4444'),
    _extKpi('ยอดเกินรวม', fmt(s.over_total || 0), 'เงินเกินลิ้นชัก', '#10b981'),
    _extKpi('ระเบียบเงินสด', ((s.day_branch ? (1 - (s.short_days || 0) / s.day_branch) * 100 : 100)).toFixed(0) + '%', 'วัน-สาขาที่ไม่ขาด', '#0891b2'),
  ].join('');
  const ccMon = d.month || '';
  // DV2: by the size of the difference either way (a large over is a control or data signal too); branches under ฿1,000 either way are
  // folded behind "ดูทั้งหมด"; an over from ฿1,000 is amber, a short red
  const ccRows = (d.branches || []).slice().sort((a, b) => Math.abs(b.over_short || 0) - Math.abs(a.over_short || 0));
  const ccKeep = getBranch() ? ccRows.length : ccRows.filter(r => Math.abs(r.over_short || 0) >= 1000).length;   // one branch: no fold
  const ccInk = v => v < 0 ? 'var(--bad-ink)' : v >= 1000 ? 'var(--amber-ink)' : 'var(--text-secondary)';
  document.getElementById('ccMore').innerHTML = ccRows.length > ccKeep
    ? (ccKeep ? '' : `<p class="text-xs py-2" style="color:var(--text-muted)">ไม่มีสาขาที่ขาดหรือเกินตั้งแต่ ฿1,000</p>`) + dvMoreBtn('ccBody', ccRows.length, ccKeep) : '';
  document.getElementById('ccBody').innerHTML = ccRows.map((r, i) => `<tr class="border-t border-slate-100 hover:bg-rose-50/40 cursor-pointer"${dvMoreAttr('ccBody', i, ccRows.length, ccKeep)} onclick="cashDayDrill('${jsAttr(r.branch)}','${ccMon}')" title="คลิกดูรายวันของ ${esc(r.branch)}">
    <td class="py-1.5 pr-2 font-medium">${esc(r.branch)} <span class="text-slate-300">›</span></td>
    <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${ccInk(r.over_short || 0)}">${(r.over_short || 0) < 0 ? 'ขาด ' : (r.over_short || 0) > 0 ? 'เกิน ' : ''}${fmt(Math.abs(r.over_short || 0))}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${r.short_days || 0}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmt(r.sales)}</td>
    <td class="py-1.5 text-right tabular-nums">${r.sales ? (r.over_short / r.sales * 100).toFixed(2) + '%' : '—'}</td>
  </tr>`).join('') || '<tr><td colspan="5" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
  document.getElementById('ccWorst').innerHTML = (d.worst_days || []).map(r => `<tr class="border-t border-slate-100 hover:bg-rose-50/40 cursor-pointer" onclick="cashWorstDayDrill('${jsAttr(r.branch)}','${jsAttr(r.day)}')" title="คลิกดูรายละเอียดของ ${esc(r.branch)} วันที่ ${esc(r.day)}">
    <td class="py-1.5 pr-2 tabular-nums">${esc(r.day)} <span class="text-slate-300">›</span></td><td class="py-1.5 pr-2">${esc(r.branch)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${r.over_short < 0 ? 'var(--bad-ink)' : 'var(--good-ink)'}">${fmt(r.over_short)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmt(r.sales)}</td><td class="py-1.5 text-right tabular-nums">${fmtN(r.pax)}</td>
  </tr>`).join('') || '<tr><td colspan="5" class="py-4 text-center text-slate-400">ไม่มีวันที่ขาด/เกิน</td></tr>';
}

// ประเภทลูกค้า
async function loadCustType(force) {
  periodHeadline('phd-custtype', 'custtype');   // PR-PP2: the top bar's month (a month-grain source)
  await _extraMonths('ctMonth');
  const mv = document.getElementById('ctMonth')?.value || '';
  const _fk = _filterKey() + '|' + mv;
  if (!force && _tabLastKey.custtype === _fk) return; _tabLastKey.custtype = _fk;
  const p = new URLSearchParams(); if (mv) p.set('month', mv);
  if (getBranch()) p.set('branch', getBranch()); if (getGroup()) p.set('group', getGroup());
  let d; try { d = await api(`${API}/api/extra/customer-type?${p}`); } catch (e) { return; }
  if (d.month) { const el = document.getElementById('ctMonth'); if (el && !el.value) el.value = d.month; }
  document.getElementById('ctRange').textContent = d.month ? `เดือน ${d.month}` : '';
  const types = d.types || [];
  const dineIn = types.filter(t => /ลูกค้าทั่วไป|เครดิต/.test(t.type)).reduce((a, b) => a + (b.customers || 0), 0);
  const deliv = types.filter(t => /Grab|Line|Food|Delivery/i.test(t.type)).reduce((a, b) => a + (b.customers || 0), 0);
  const tc = d.total_customers || 1;
  document.getElementById('ctKpis').innerHTML = [
    _extKpi('ยอดรวม (ex-VAT)', fmt(d.total_net || 0), 'ทุกประเภทเดือนนี้', '#4a70b0'),
    _extKpi('ทานที่ร้าน/เครดิต', (dineIn / tc * 100).toFixed(1) + '%', fmtN(dineIn) + ' ราย', '#10b981'),
    _extKpi('เดลิเวอรี (Grab/LINE)', (deliv / tc * 100).toFixed(1) + '%', fmtN(deliv) + ' ราย', '#f59e0b'),
    _extKpi('จำนวนประเภท', String(types.length), 'ช่องทางลูกค้า', '#0891b2'),
  ].join('');
  const maxC = Math.max(1, ...types.map(t => t.customers || 0));
  const palette = ['#4a70b0', '#10b981', '#f59e0b', '#ef4444', '#0891b2', '#a855f7', '#64748b'];
  document.getElementById('ctBars').innerHTML = types.map((t, i) => `
    <div class="flex items-center gap-2 mb-2">
      <span class="text-[11px] w-28 shrink-0 truncate" style="color:var(--text-secondary)" title="${esc(t.type)}">${esc(t.type)}</span>
      <div class="flex-1 rounded-full h-4" style="background:var(--bg-subtle)"><div class="h-4 rounded-full" style="width:${(t.customers || 0) / maxC * 100}%;background:${palette[i % palette.length]}"></div></div>
      <span class="text-[11px] tabular-nums w-12 text-right font-semibold" style="color:var(--text-primary)">${t.cust_pct}%</span>
    </div>`).join('') || '<p class="text-slate-400 text-center py-4">ไม่มีข้อมูล</p>';
  document.getElementById('ctBody').innerHTML = types.map(t => `<tr class="border-t border-slate-100 hover:bg-indigo-50/40">
    <td class="py-1.5 pr-2 font-medium">${esc(t.type)}</td><td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmtN(t.customers)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums font-semibold">${t.cust_pct}%</td><td class="py-1.5 pr-2 text-right tabular-nums">${fmt(t.net)}</td>
    <td class="py-1.5 text-right tabular-nums font-semibold">${t.net_pct}%</td>
  </tr>`).join('') || '<tr><td colspan="5" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
  // DV2: the by-branch table answers "which branch sells through delivery"; with delivery at 0 % in every branch it answers nothing and hides
  const ctRows = d.by_branch || [];
  const ctCard = document.getElementById('ctBranchCard');
  if (ctCard) ctCard.style.display = ctRows.length && ctRows.every(r => (r.delivery_pct || 0) < 0.5) ? 'none' : '';
  document.getElementById('ctByBranch').innerHTML = ctRows.map(r => {
    const dCol = r.delivery_pct >= 25 ? '#f59e0b' : r.delivery_pct >= 10 ? '#0891b2' : '#94a3b8';
    return `<tr class="border-t border-slate-100 hover:bg-indigo-50/40">
      <td class="py-1.5 pr-2 font-medium">${esc(r.branch)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-400">${fmtN(r.customers)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmtN(r.delivery_cust)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${textInk(dCol)}">${r.delivery_pct}%</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(r.net)}</td>
      <td class="py-1.5 text-right tabular-nums font-semibold">${r.net_pct != null ? r.net_pct + '%' : '—'}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
}

// ความต้องการพิเศษ (modifiers)
async function loadModifiers(force) {
  const _fk = _filterKey();
  if (!force && _tabLastKey.modifiers === _fk) return; _tabLastKey.modifiers = _fk;
  const p = new URLSearchParams(); p.set('limit', '25');
  if (getBranch()) p.set('branch', getBranch()); if (getGroup()) p.set('group', getGroup());
  let d; try { d = await api(`${API}/api/extra/modifiers?${p}`); } catch (e) { return; }
  document.getElementById('modTotal').textContent = `รวม ${fmtN(d.total || 0)} ครั้ง`;
  const ch = d.choices || []; const maxN = Math.max(1, ...ch.map(x => x.n || 0));
  document.getElementById('modBars').innerHTML = ch.map((x, i) => `
    <div class="flex items-center gap-2 mb-2">
      <span class="text-[11px] w-6 text-right tabular-nums" style="color:var(--text-muted)">${i + 1}</span>
      <span class="text-xs w-52 shrink-0 truncate" style="color:var(--text-secondary)" title="${esc(x.choice)}">${esc(x.choice)}</span>
      <div class="flex-1 rounded-full h-4" style="background:var(--bg-subtle)"><div class="h-4 rounded-full" style="width:${(x.n || 0) / maxN * 100}%;background:var(--accent)"></div></div>
      <span class="text-[11px] tabular-nums w-16 text-right font-semibold" style="color:var(--text-primary)">${fmtN(x.n)}</span>
      <span class="text-[10px] tabular-nums w-10 text-right" style="color:var(--text-muted)">${x.pct}%</span>
    </div>`).join('') || '<p class="text-slate-400 text-center py-4">ไม่มีข้อมูล</p>';
}

// ราคา & โซนโต๊ะ
async function loadPriceZone(force) {
  periodHeadline('phd-pricezone', 'pricezone');   // PR-PP2: the top bar's month
  await _extraMonths('pzMonth');
  const mv = document.getElementById('pzMonth')?.value || '';
  const _fk = _filterKey() + '|' + mv;
  if (!force && _tabLastKey.pricezone === _fk) return; _tabLastKey.pricezone = _fk;
  const p = new URLSearchParams(); p.set('limit', '200'); if (mv) p.set('month', mv);
  if (getBranch()) p.set('branch', getBranch()); if (getGroup()) p.set('group', getGroup());
  let d; try { d = await api(`${API}/api/extra/price-zone?${p}`); } catch (e) { return; }
  document.getElementById('pzCount').textContent = `แก้ราคา ${fmtN(d.change_count || 0)} ครั้ง${(d.changes || []).length >= 200 ? ' (แสดง 200 ล่าสุด)' : ''}`;
  // DV2: no price change in the period -> one line, not an empty table
  const pzNone = !(d.changes || []).length, pzE = document.getElementById('pzEmpty'), pzW = document.getElementById('pzTableWrap');
  if (pzE) pzE.style.display = pzNone ? '' : 'none';
  if (pzW) pzW.style.display = pzNone ? 'none' : '';
  document.getElementById('pzBody').innerHTML = (d.changes || []).map(r => {
    const dn = (r.delta || 0) < 0;
    const qp = r.qty_pct;
    const qpHtml = qp == null ? '<span class="text-slate-300">—</span>'
      : `<span style="color:${qp > 0 ? 'var(--good-ink)' : qp < 0 ? 'var(--bad-ink)' : 'var(--text-muted)'}">${qp > 0 ? '+' : ''}${qp.toFixed(1)}%</span>`;
    const baq = (r.before_qty != null) ? `${fmtN(r.before_qty)}→${fmtN(r.after_qty)}` : '—';
    return `<tr class="border-t border-slate-100 hover:bg-indigo-50/40">
    <td class="py-1.5 pr-2 tabular-nums text-[10.5px]">${esc((r.date || '').replace('T', ' ').slice(0, 16))}</td>
    <td class="py-1.5 pr-2">${esc(r.branch)}</td><td class="py-1.5 pr-2">${esc(r.item || '')}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmt(r.default_price)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(r.new_price)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${dn ? 'var(--bad-ink)' : 'var(--good-ink)'}">${dn ? '' : '+'}${fmt(r.delta)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${baq}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${qpHtml}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${r.elasticity != null ? r.elasticity.toFixed(2) : '<span class="text-slate-300">—</span>'}</td>
    <td class="py-1.5 text-[11px]">${esc(r.user || '')}</td></tr>`;
  }).join('') || '<tr><td colspan="10" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
  _pzZonesHtml(d.zones);
}
// DV2: โซนโต๊ะรายสาขา lives in ตั้งค่า → สาขา (reference data); loadSettingsSubtab('branches') calls loadPzZonesRef
function _pzZonesHtml(zones) {
  const el = document.getElementById('pzZones');
  if (!el) return;
  el.innerHTML = (zones || []).map(r => `<tr class="border-t border-slate-100 hover:bg-indigo-50/40">
    <td class="py-1.5 pr-2 font-medium">${esc(r.branch)}</td><td class="py-1.5 pr-2 text-right tabular-nums">${r.zones}</td>
    <td class="py-1.5 text-right tabular-nums">${fmtN(r.tables)}</td></tr>`).join('') || '<tr><td colspan="3" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
}
async function loadPzZonesRef() {
  if (!document.getElementById('pzZones') || (typeof pathAllowedForMe === 'function' && !pathAllowedForMe('/api/extra/price-zone'))) return;
  const p = new URLSearchParams(); p.set('limit', '1');
  if (getGroup()) p.set('group', getGroup());
  try { const d = await api(`${API}/api/extra/price-zone?${p}`); _pzZonesHtml(d.zones); } catch (e) { /* the card keeps its last rows */ }
}

// บิลยกเลิก/ลบ (whole-bill voids)
async function loadCancelBill(force) {
  periodHeadline('phd-cancelbill', 'cancelbill');   // PR-PP2: the top bar's month
  await _extraMonths('cbMonth');
  const mv = document.getElementById('cbMonth')?.value || '';
  const _fk = _filterKey() + '|' + mv;
  if (!force && _tabLastKey.cancelbill === _fk) return; _tabLastKey.cancelbill = _fk;
  const p = new URLSearchParams(); if (mv) p.set('month', mv);
  if (getBranch()) p.set('branch', getBranch()); if (getGroup()) p.set('group', getGroup());
  let d; try { d = await api(`${API}/api/extra/cancel-bills?${p}`); } catch (e) { return; }
  if (d.month) { const el = document.getElementById('cbMonth'); if (el && !el.value) el.value = d.month; }
  document.getElementById('cbRange').textContent = d.month ? `เดือน ${d.month}` : '';
  const s = d.summary || {};
  // Whole-bill void rate bands (owner 2026-10-22): normal <= 0.5%, watch 0.5-1%, high > 1% (same as ร้านของฉัน, routers/franchise.py T_CANCEL_*).
  const cr = s.cancel_rate;
  const crColor = cr == null ? '#7a66b8' : cr > 1 ? '#ef4444' : cr > 0.5 ? '#f59e0b' : '#10b981';
  const crSub = cr == null ? 'ของบิลทั้งหมด'
    : cr > 1 ? `สูง · เกณฑ์ปกติไม่เกิน 0.5% ●` : cr > 0.5 ? `ควรเฝ้าระวัง · ปกติไม่เกิน 0.5% ●` : `ปกติดี (ไม่เกิน 0.5%) ●`;
  document.getElementById('cbKpis').innerHTML = [
    _extKpi('บิลยกเลิก/ลบ', fmtN(s.bills || 0), 'ทั้งบิล (เดือนนี้)', '#ef4444'),
    _extKpi('มูลค่ารวม', fmt(s.amount || 0), 'ยอดบิลที่ถูกยกเลิก', '#f59e0b'),
    _extKpi('อัตรายกเลิก', cr != null ? cr + '%' : '—', crSub, crColor),
    _extKpi('บิลปกติ', fmtN(s.valid_bills || 0), 'เทียบเป็นฐาน', '#0891b2'),
  ].join('');
  const cbAlert = document.getElementById('cbAlert');
  if (cbAlert) cbAlert.innerHTML = (cr != null && cr > 0.5)
    ? `<div class="card p-3 text-xs" style="background:${cr>1?'#fef2f2':'#fffbeb'};border:1px solid ${cr>1?'#ef4444':'#f59e0b'}">⚠ อัตรายกเลิกบิล <b>${cr}%</b> ${cr>1?'สูงกว่าเกณฑ์ปกติ (ไม่เกิน 0.5%) อย่างชัดเจน':'เกินเกณฑ์ปกติเล็กน้อย'} — คลิกสาขาด้านล่างเพื่อดูบิลรายใบและตรวจสอบเหตุผล (loss prevention)</div>`
    : '';
  const tb = (s.bills || 1);
  document.getElementById('cbReasons').innerHTML = (d.reasons || []).map(r => `<tr class="border-t border-slate-100 hover:bg-rose-50/40">
    <td class="py-1.5 pr-2">${esc(r.reason)}${r.abnormal ? ' <span class="pill pill-warn" title="รหัสนี้ไม่มีในรายการเหตุผลของ POS">ข้อมูลผิดปกติ</span>' : ''}</td><td class="py-1.5 pr-2 text-right tabular-nums font-semibold">${fmtN(r.bills)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${(r.bills / tb * 100).toFixed(1)}%</td>
    <td class="py-1.5 text-right tabular-nums" style="color:var(--bad-ink)">${fmt(r.amount)}</td></tr>`).join('')
    || '<tr><td colspan="4" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
  const cbMon = d.month || '';
  // DV2: by value with each branch's share of the chain's cancelled value; branches from ฿50,000 (at least 5) show, the rest behind "ดูทั้งหมด"
  const cbRows = (d.branches || []).slice().sort((a, b) => (b.amount || 0) - (a.amount || 0) || (b.bills || 0) - (a.bills || 0));
  const cbTot = cbRows.reduce((a, r) => a + (r.amount || 0), 0) || 0;
  const cbKeep = Math.max(5, cbRows.filter(r => (r.amount || 0) >= 50000).length);
  document.getElementById('cbBranches').innerHTML = cbRows.map((r, i) => `<tr class="border-t border-slate-100 hover:bg-rose-50/40 cursor-pointer"${dvMoreAttr('cbBranches', i, cbRows.length, cbKeep)} onclick="cancelBillDrill('${jsAttr(r.branch)}','${cbMon}')" title="คลิกดูบิลที่ถูกยกเลิกของ ${esc(r.branch)}">
    <td class="py-1.5 pr-2 font-medium">${esc(r.branch)} <span class="text-slate-300">›</span></td>
    <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:var(--bad-ink)">${fmt(r.amount)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${cbTot ? ((r.amount || 0) / cbTot * 100).toFixed(1) + '%' : '—'}</td>
    <td class="py-1.5 text-right tabular-nums">${fmtN(r.bills)}</td></tr>`).join('')
    || '<tr><td colspan="4" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
  document.getElementById('cbBranchMore').innerHTML = dvMoreBtn('cbBranches', cbRows.length, cbKeep);
}

// Drill: per-DAY cash over/short for one branch (from the cash by-branch table).
async function cashDayDrill(branch, month) {
  const modal = document.getElementById('diveModal'), title = document.getElementById('diveTitle'), body = document.getElementById('diveBody');
  if (!modal) return;
  title.textContent = `เงินสดขาด/เกิน รายวัน · ${branch} · ${month}`;
  body.innerHTML = '<p class="text-sm text-slate-400" style="padding:24px">กำลังโหลด…</p>';
  modal.classList.remove('hidden');
  let d; try { d = await api(`${API}/api/extra/cash/detail?branch=${encodeURIComponent(branch)}&month=${encodeURIComponent(month)}`); }
  catch (e) { body.innerHTML = `<p class="text-sm text-red-500" style="padding:24px">โหลดไม่สำเร็จ: ${e.message}</p>`; return; }
  const days = d.days || [];
  const net = days.reduce((a,x)=>a+(x.over_short||0),0);
  const shortDays = days.filter(x=>x.over_short<0).length;
  const rows = days.map(x => `<tr class="border-t border-slate-100">
    <td class="py-1.5 pr-3 tabular-nums">${esc(x.day)}</td>
    <td class="py-1.5 pr-3 text-right tabular-nums font-semibold" style="color:${x.over_short<0?'var(--bad-ink)':x.over_short>0?'var(--good-ink)':'var(--text-muted)'}">${fmt(x.over_short)}</td>
    <td class="py-1.5 pr-3 text-right tabular-nums text-slate-500">${fmt(x.sales)}</td>
    <td class="py-1.5 pr-3 text-right tabular-nums">${fmtN(x.pax)}</td>
    <td class="py-1.5 pr-3 text-right tabular-nums text-slate-500">${fmt(x.discount)}</td>
    <td class="py-1.5 text-right tabular-nums text-slate-500">${fmt(x.cancel)}</td></tr>`).join('');
  body.innerHTML = `<div style="padding:4px 8px">
    <div class="flex gap-4 text-sm mb-3 flex-wrap">
      <span>ขาด/เกินสุทธิ: <b style="color:${net<0?'var(--bad-ink)':'var(--good-ink)'}">${fmt(net)}</b></span>
      <span class="text-slate-500">วันที่ขาด: <b>${shortDays}</b>/${days.length}</span>
    </div>
    <div style="max-height:60vh;overflow:auto"><table class="text-xs w-full"><thead class="sticky top-0" style="background:var(--bg-card)"><tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">
      <th class="py-1.5 pr-3">วันที่</th><th class="py-1.5 pr-3 text-right">ขาด/เกิน</th><th class="py-1.5 pr-3 text-right">ยอดขาย</th>
      <th class="py-1.5 pr-3 text-right">ลูกค้า</th><th class="py-1.5 pr-3 text-right">ส่วนลด</th><th class="py-1.5 text-right">ยกเลิก</th>
    </tr></thead><tbody>${rows || '<tr><td colspan="6" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>'}</tbody></table></div>
    <button onclick="closeDive()" class="dive-action-btn" style="margin-top:12px">✕ ปิด (Esc)</button></div>`;
}

// Drill from a "วันที่ขาดหนักสุด" row → one branch + one day. Cash variance has no
// per-bill grain, so we show the day's headline + the voided bills on that exact day
// (the usual cause of a drawer short) so the owner can investigate the specific day.
async function cashWorstDayDrill(branch, day) {
  const modal = document.getElementById('diveModal'), title = document.getElementById('diveTitle'), body = document.getElementById('diveBody');
  if (!modal) return;
  title.textContent = `เงินสดขาด/เกิน · ${branch} · ${fmtDateTh(day)}`;
  body.innerHTML = '<p class="text-sm text-slate-400" style="padding:24px">กำลังโหลด…</p>';
  modal.classList.remove('hidden');
  let d; try { d = await api(`${API}/api/extra/cash/day?branch=${encodeURIComponent(branch)}&day=${encodeURIComponent(day)}`); }
  catch (e) { body.innerHTML = `<p class="text-sm text-red-500" style="padding:24px">โหลดไม่สำเร็จ: ${e.message}</p>`; return; }
  const h = d.head || {};
  const kpi = (label, val, col) => `<div class="card p-3" style="background:var(--bg-subtle)">
    <div class="text-[11px]" style="color:var(--text-muted)">${label}</div>
    <div class="text-base font-semibold tabular-nums"${col?` style="color:${textInk(col)}"`:''}>${val}</div></div>`;
  const os = h.over_short || 0;
  const kpis = `<div class="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
    ${kpi('ขาด/เกิน', fmt(os), os<0?'#ef4444':os>0?'#10b981':'#94a3b8')}
    ${kpi('ยอดขาย', fmt(h.sales||0))}
    ${kpi('ลูกค้า', fmtN(h.pax||0))}
    ${kpi('ส่วนลด', fmt(h.discount||0), '#f59e0b')}
    ${kpi('ยกเลิก', fmt(h.cancel||0), '#ef4444')}
    ${kpi('จำนวนบิล', fmtN(h.invoices||0))}</div>`;
  const bills = d.cancel_bills || [];
  let billsHtml;
  if (d.cancel_staged === false) {
    billsHtml = '<p class="text-xs" style="color:var(--amber-ink)">บิลรายใบจะแสดงหลังการซิงก์ครั้งถัดไป</p>';
  } else {
    const total = bills.reduce((a,x)=>a+(x.amount||0),0);
    const rows = bills.map(x => `<tr class="border-t border-slate-100">
      <td class="py-1.5 pr-3 tabular-nums text-[11px]">${esc((x.sale_date||'').replace('T',' ').slice(11,19)||'—')}</td>
      <td class="py-1.5 pr-3 font-mono text-[11px]">${esc(x.receipt_no)}</td>
      <td class="py-1.5 pr-3">${esc(x.reason)}</td>
      <td class="py-1.5 pr-3">${x.cancel_user ? esc(x.cancel_user) : '<span class="text-slate-300">—</span>'}</td>
      <td class="py-1.5 text-right tabular-nums" style="color:var(--bad-ink)">${fmt(x.amount)}</td></tr>`).join('');
    billsHtml = `<h3 class="text-xs font-semibold mb-2" style="color:var(--text-secondary)">บิลที่ถูกยกเลิก/ลบ ในวันนี้ · ${fmtN(bills.length)} ใบ · รวม <span style="color:var(--bad-ink)">${fmt(total)}</span></h3>
      <div style="max-height:48vh;overflow:auto"><table class="text-xs w-full"><thead class="sticky top-0" style="background:var(--bg-card)"><tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">
        <th class="py-1.5 pr-3">เวลา</th><th class="py-1.5 pr-3">เลขบิล</th><th class="py-1.5 pr-3">เหตุผล</th><th class="py-1.5 pr-3">ผู้ลบบิล</th><th class="py-1.5 text-right">มูลค่า</th>
      </tr></thead><tbody>${rows || '<tr><td colspan="5" class="py-4 text-center text-slate-400">ไม่มีบิลที่ถูกยกเลิกในวันนี้</td></tr>'}</tbody></table></div>`;
  }
  body.innerHTML = `<div style="padding:4px 8px">${kpis}${billsHtml}
    <button onclick="closeDive()" class="dive-action-btn" style="margin-top:12px">✕ ปิด (Esc)</button></div>`;
}

// Drill: INDIVIDUAL voided bills for one branch (check each error). Needs the
// pos_cancel_bill_detail table (staged by sync_mssql) — shows a hint if not yet synced.
async function cancelBillDrill(branch, month) {
  const modal = document.getElementById('diveModal'), title = document.getElementById('diveTitle'), body = document.getElementById('diveBody');
  if (!modal) return;
  title.textContent = `บิลที่ถูกยกเลิก/ลบ · ${branch}${month?' · '+month:''}`;
  body.innerHTML = '<p class="text-sm text-slate-400" style="padding:24px">กำลังโหลด…</p>';
  modal.classList.remove('hidden');
  let d; try { d = await api(`${API}/api/extra/cancel-bills/detail?branch=${encodeURIComponent(branch)}${month?'&month='+encodeURIComponent(month):''}`); }
  catch (e) { body.innerHTML = `<p class="text-sm text-red-500" style="padding:24px">โหลดไม่สำเร็จ: ${e.message}</p>`; return; }
  if (d.staged === false) {
    body.innerHTML = `<div style="padding:24px"><p class="text-sm text-slate-600">ยังไม่มีข้อมูลบิลรายใบ — ตารางรายละเอียดจะถูกสร้างเมื่อ <b>ซิงก์ครั้งถัดไป</b> (Sync Live Data / งาน 9 โมง). หลังจากนั้นคลิกสาขาจะเห็นบิลแต่ละใบที่ถูกยกเลิกพร้อมเหตุผล</p>
      <button onclick="closeDive()" class="dive-action-btn" style="margin-top:12px">✕ ปิด (Esc)</button></div>`;
    return;
  }
  const bills = d.bills || [];
  const total = bills.reduce((a,x)=>a+(x.amount||0),0);
  const rows = bills.map(x => `<tr class="border-t border-slate-100">
    <td class="py-1.5 pr-3 tabular-nums text-[11px]">${esc((x.sale_date||'').replace('T',' '))}</td>
    <td class="py-1.5 pr-3 font-mono text-[11px]">${esc(x.receipt_no)}</td>
    <td class="py-1.5 pr-3">${esc(x.reason)}</td>
    <td class="py-1.5 pr-3">${x.cancel_user ? esc(x.cancel_user) : '<span class="text-slate-300">—</span>'}</td>
    <td class="py-1.5 text-right tabular-nums" style="color:var(--bad-ink)">${fmt(x.amount)}</td></tr>`).join('');
  body.innerHTML = `<div style="padding:4px 8px">
    <div class="flex gap-4 text-sm mb-3 flex-wrap">
      <span>บิลที่ยกเลิก: <b>${fmtN(bills.length)}</b>${d.capped?' (แสดงล่าสุด)':''}</span>
      <span class="text-slate-500">มูลค่ารวม: <b style="color:var(--bad-ink)">${fmt(total)}</b></span>
    </div>
    <div style="max-height:60vh;overflow:auto"><table class="text-xs w-full"><thead class="sticky top-0" style="background:var(--bg-card)"><tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">
      <th class="py-1.5 pr-3">วันเวลา</th><th class="py-1.5 pr-3">เลขบิล</th><th class="py-1.5 pr-3">เหตุผล</th><th class="py-1.5 pr-3">ผู้ลบบิล <span class="text-[10px] text-slate-400">(login)</span></th><th class="py-1.5 text-right">มูลค่า</th>
    </tr></thead><tbody>${rows || '<tr><td colspan="5" class="py-4 text-center text-slate-400">ไม่มีบิลที่ถูกยกเลิกในช่วงนี้</td></tr>'}</tbody></table></div>
    <button onclick="closeDive()" class="dive-action-btn" style="margin-top:12px">✕ ปิด (Esc)</button></div>`;
}

// เวลาปิดร้าน / ความสดของข้อมูล (data freshness per branch; optional single-day mode)
// Data-integrity findings — reported, never auto-repaired (real financial
// records; missing days can only come back from a POS re-sync).
async function loadDataQuality() {
  const el = document.getElementById('dqFindings'); if (!el) return;
  let d; try { d = await api(`${API}/api/data-quality`); }
  catch (e) { el.innerHTML = '<p class="text-xs text-slate-400">โหลดไม่สำเร็จ</p>'; return; }
  // PL3 (RV3 F6): the cards take the page's own tokens - a tint of the severity colour over the card fill - not a fixed cream/pink
  // fill, which was a white block inside a dark page
  const sev = { high: ['#dc2626', 'color-mix(in srgb, #dc2626 9%, var(--bg-card))'], warn: ['#d97706', 'color-mix(in srgb, #d97706 9%, var(--bg-card))'],
                info: ['#0891b2', 'color-mix(in srgb, #0891b2 9%, var(--bg-card))'] };
  const rowLine = (k, r) => {
    if (k === 'missing_days') return `${esc(r.branch)} — ${r.missing_days} วัน (${(r.dates || []).slice(0, 3).join(', ')}${(r.dates || []).length > 3 ? ' …' : ''})`;
    // Recorded closures are shown WITH their reason: the point of splitting them
    // out is that a wrong entry stays auditable, not that the days disappear.
    if (k === 'known_downtime') return `${esc(r.branch)} — ${r.missing_days} วัน · <b>${esc(r.reason || '')}</b> (${(r.dates || []).slice(0, 3).join(', ')}${(r.dates || []).length > 3 ? ' …' : ''})`;
    if (k === 'zero_covers') return `${esc(r.br)} — ${fmtN(r.n)} บิล · ฿${fmtN(r.rev)}`;
    if (k === 'discount_gt_total') return `${esc(r.br)} — ${fmtN(r.n)} บิล · ส่วนลด ฿${fmtN(r.disc)} เทียบยอดบิล ฿${fmtN(r.tot)}`;
    return '';
  };
  el.innerHTML = (d.findings || []).map(f => {
    const [c, bg] = sev[f.severity] || sev.info;
    const rows = (f.rows || []).map(r => `<li class="text-[11px]" style="color:var(--text-muted)">• ${rowLine(f.key, r)}</li>`).join('');
    return `<div class="mb-2 rounded-lg p-3" style="background:${f.benign ? 'var(--bg-subtle)' : bg};border:1px solid ${f.benign ? 'var(--border)' : c}">
        <div class="flex items-baseline justify-between gap-3 flex-wrap">
          <span class="text-sm font-semibold" style="color:var(--text-primary)">${esc(f.title)}
            ${f.benign ? '<span class="text-[10px] font-normal" style="color:var(--text-muted)">· ไม่กระทบตัวเลขการเงิน</span>' : ''}</span>
          <span class="text-lg font-bold tabular-nums" style="color:${f.benign ? 'var(--text-muted)' : textInk(c)}">${fmtN(f.count)}</span>
        </div>
        <p class="text-[11px] mt-1" style="color:var(--text-muted)">${esc(f.detail)}</p>
        ${rows ? `<ul class="mt-1 space-y-0.5">${rows}</ul>` : ''}
        ${f.key === 'uncosted_skus' ? `<a href="${API}/api/cost/suggestions.csv" download
             class="inline-block mt-2 text-[11px] font-semibold rounded px-2 py-1"
             style="background:var(--bg-card);border:1px solid ${c};color:${textInk(c)}">ดาวน์โหลดต้นทุนที่ระบบประมาณให้ (CSV)</a>
           <span class="text-[10px] ml-2" style="color:var(--text-muted)">ประมาณจากเมนูที่มีต้นทุนจริงแล้ว · ตรวจก่อนนำไปกรอก</span>` : ''}
      </div>`;
  }).join('') || '<p class="text-xs text-slate-400">ไม่พบปัญหา</p>';
}

// Last payload + chart handles for the tab, so the dive-in panels can read the
// real numbers instead of re-deriving them from the chart datasets.
let _ctmLast = null, _ctmChartDays = null, _ctmChartClose = null;

async function loadCloseTime(force) {
  loadDataQuality();                          // integrity panel above the freshness table
  const dateSel = document.getElementById('ctmDate')?.value || '';
  const _fk = _filterKey() + '|' + dateSel;
  if (!force && _tabLastKey.closetime === _fk) return; _tabLastKey.closetime = _fk;
  const p = new URLSearchParams();
  if (getBranch()) p.set('branch', getBranch()); if (getGroup()) p.set('group', getGroup());
  if (dateSel) p.set('date', dateSel);
  let d; try { d = await api(`${API}/api/extra/closing-time?${p}`); } catch (e) { return; }
  const s = d.summary || {};
  const head = document.getElementById('ctmHead');
  const title = document.getElementById('ctmTitle');
  // Constrain the date picker to the available data window.
  const di = document.getElementById('ctmDate');
  if (di) { if (d.data_min_date) di.min = d.data_min_date; if (d.today) di.max = d.today; }
  document.getElementById('ctmFresh').textContent =
    d.data_max ? `ข้อมูลล่าสุดทั้งระบบ: ${d.data_max}  ·  วันนี้: ${d.today}` : 'ไม่มีข้อมูล';
  const TH = (cols) => '<tr class="text-left border-b border-slate-200" style="color:var(--text-muted)">' +
    cols.map(c => `<th class="py-1.5 pr-2${c.r ? ' text-right' : ''}">${c.t}</th>`).join('') + '</tr>';

  _ctmLast = d;
  // Charts only make sense in freshness mode — single-day mode is a snapshot,
  // and a 30-day series under a one-day question is just noise.
  const chartsEl = document.getElementById('ctmCharts');
  if (chartsEl) chartsEl.style.display = d.mode === 'date' ? 'none' : '';

  if (d.mode === 'date') {
    // ── Single-day mode ──
    title.textContent = `รายสาขา — วันที่ ${d.date} (บนสุด = ไม่มีข้อมูลวันนี้)`;
    document.getElementById('ctmKpis').innerHTML = [
      _extKpi('สาขาทั้งหมด', fmtN(s.branch_count || 0), 'ที่มีข้อมูลขาย', '#0891b2'),
      _extKpi('มีข้อมูลวันนี้', fmtN(s.reported || 0), `วันที่ ${d.date}`, '#10b981'),
      _extKpi('ไม่มีข้อมูล', fmtN(s.missing || 0), 'สาขาที่ไม่มีบิลวันนี้', s.missing ? '#ef4444' : '#94a3b8'),
      _extKpi('บิลรวมวันนี้', fmtN(s.day_bills || 0), 'ทุกสาขารวมกัน', '#7a66b8'),
    ].join('');
    head.innerHTML = TH([{t:'สาขา'},{t:'สถานะ'},{t:'เวลาปิด (วันนี้)',r:1},{t:'บิล (วันนี้)',r:1},{t:'ข้อมูลล่าสุด (all-time)'}]);
    document.getElementById('ctmRows').innerHTML = (d.branches || []).map(r => {
      const rep = r.status === 'reported';
      const bdg = rep
        ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold" style="background:#dcfce7;color:#15803d">มีข้อมูล</span>'
        : '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold" style="background:#fee2e2;color:#b91c1c">ไม่มีข้อมูล</span>';
      return `<tr class="border-t border-slate-100 hover:bg-amber-50/40${rep ? '' : ' mi-outlier'}">
        <td class="py-1.5 pr-2 font-medium" data-sort="${esc(r.branch)}">${esc(r.branch)}</td>
        <td class="py-1.5 pr-2" data-sort="${rep ? 1 : 0}">${bdg}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${r.day_close || '—'}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${rep ? 'var(--text-primary)' : 'var(--bad-ink)'}">${rep ? fmtN(r.day_bills) : '0'}</td>
        <td class="py-1.5 pr-2 tabular-nums" style="color:var(--text-muted)">${esc(r.last_sold || '—')}</td>
      </tr>`;
    }).join('') || '<tr><td colspan="5" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
    makeSortable('ctmTable');
    return;
  }

  // ── Latest / freshness mode (default) ──
  title.textContent = 'รายสาขา — เรียงตามความล่าช้า (บนสุด = ค้างนานสุด)';
  document.getElementById('ctmKpis').innerHTML = [
    _extKpi('สาขาทั้งหมด', fmtN(s.branch_count || 0), 'ที่มีข้อมูลขาย', '#0891b2'),
    _extKpi('อัปเดตล่าสุด', fmtN(s.current || 0), 'ข้อมูลถึงวันล่าสุด', '#10b981'),
    _extKpi('ข้อมูลล่าช้า', fmtN(s.behind || 0), 'ยังไม่ sync วันล่าสุด', s.behind ? '#ef4444' : '#94a3b8'),
    _extKpi('ค้างนานสุด', (s.max_days_behind || 0) + ' วัน', 'จากวันข้อมูลล่าสุด', s.max_days_behind ? '#f59e0b' : '#94a3b8'),
    _extKpi('วันที่ข้อมูลไม่ครบ', fmtN(s.gap_days || 0),
            `จาก ${fmtN(s.window_days || 0)} วันล่าสุด`, s.gap_days ? '#ef4444' : '#10b981'),
  ].join('');
  head.innerHTML = TH([{t:'สาขา'},{t:'สถานะ'},{t:'ข้อมูลล่าสุด'},{t:'ล่าช้า (วัน)',r:1},{t:'เวลาปิดเฉลี่ย',r:1},{t:'วันเปิด (30วัน)',r:1},{t:'บิลรวม',r:1}]);
  const badge = st => {
    if (st === 'current')  return '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold" style="background:#dcfce7;color:#15803d">อัปเดต</span>';
    if (st === 'inactive') return '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold" style="background:#e2e8f0;color:#475569">ไม่เคลื่อนไหว</span>';
    return '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold" style="background:#fee2e2;color:#b91c1c">ล่าช้า</span>';
  };
  // Two independent anomalies on this table: a branch far behind the others on
  // sync, and one whose shop closes at a time unlike the rest of the chain.
  const brs = d.branches || [];
  const closeOut = _miOutlierSet(brs.map(r => r.avg_close_min));
  const openOut = _miOutlierSet(brs.map(r => r.days_open));
  document.getElementById('ctmRows').innerHTML = brs.map((r, i) => {
    const db = r.days_behind;
    const dbCol = db === 0 ? '#10b981' : db >= 14 ? '#64748b' : '#ef4444';
    const lateClose = closeOut.has(i);
    const fewDays = openOut.has(i) && r.days_open < 20;
    const rowCls = (db > 0 && db < 14) ? ' mi-outlier' : lateClose ? ' mi-outlier' : '';
    return `<tr class="border-t border-slate-100 hover:bg-amber-50/40${rowCls}">
      <td class="py-1.5 pr-2 font-medium" data-sort="${esc(r.branch)}">${esc(r.branch)}</td>
      <td class="py-1.5 pr-2" data-sort="${db == null ? '' : db}">${badge(r.status)}</td>
      <td class="py-1.5 pr-2 tabular-nums" style="color:var(--text-secondary)">${esc(r.last_sold || '—')}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${textInk(dbCol)}">${db == null ? '—' : db}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums" data-sort="${r.avg_close_min == null ? '' : r.avg_close_min}">${r.avg_close || '—'}${lateClose ? ' <span class="mi-flag warn" title="เวลาปิดต่างจากสาขาอื่นมาก (หลุดกรอบ Tukey)">⚠ ต่างจากเพื่อน</span>' : ''}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums" style="color:${fewDays ? 'var(--amber-ink)' : 'var(--text-muted)'}">${fmtN(r.days_open)}${fewDays ? ' <span class="mi-flag warn" title="เปิดขายน้อยวันผิดปกติใน 30 วันล่าสุด — ปิดปรับปรุง หรือข้อมูลขาด">⚠</span>' : ''}</td>
      <td class="py-1.5 text-right tabular-nums" style="color:var(--text-muted)">${fmtN(r.total_bills)}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="7" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
  makeSortable('ctmTable');
  _ctmRenderCharts(d);
}

function _ctmRenderCharts(d) {
  const daily = d.daily || [], brs = d.branches || [];
  if (daily.length) {
    // A day where a branch is missing is not "slightly lower" — it is a hole in
    // the data every other number on the dashboard is computed from, so it gets
    // its own colour rather than being read off the bar height.
    _ctmChartDays = mkChart('ctmChartDays', {
      type: 'bar',
      data: { labels: daily.map(r => r.day), datasets: [{
        label: 'สาขาที่มีข้อมูล', data: daily.map(r => r.branches),
        backgroundColor: daily.map(r => r.missing ? 'rgba(220,38,38,.75)' : 'rgba(14,116,144,.55)'),
        borderColor: daily.map(r => r.missing ? '#dc2626' : 'transparent'),
        borderWidth: daily.map(r => r.missing ? 1.5 : 0) }] },
      options: { responsive: true, maintainAspectRatio: false,
        scales: { y: { beginAtZero: true, ticks: { precision: 0 },
                       title: { display: true, text: 'จำนวนสาขา' } } },
        plugins: { tooltip: { callbacks: { afterLabel: c => {
          const r = daily[c.dataIndex];
          return `บิล ${fmtN(r.bills)}` + (r.missing ? ` · ขาด ${r.missing} สาขา` : '');
        } } } } } });
  } else if (_ctmChartDays) { _ctmChartDays.destroy(); _ctmChartDays = null; }

  const withClose = brs.filter(r => r.avg_close_min != null);
  if (withClose.length) {
    const mins = withClose.map(r => r.avg_close_min);
    const out = _miOutlierSet(mins);
    _ctmChartClose = mkChart('ctmChartClose', {
      type: 'bar',
      data: { labels: withClose.map(r => r.branch), datasets: [{
        label: 'เวลาปิดเฉลี่ย (นาทีจากเที่ยงคืน)', data: mins,
        backgroundColor: mins.map((_, i) => out.has(i) ? 'rgba(220,38,38,.75)' : 'rgba(122,102,184,.55)'),
        borderColor: mins.map((_, i) => out.has(i) ? '#dc2626' : 'transparent'),
        borderWidth: mins.map((_, i) => out.has(i) ? 1.5 : 0) }] },
      options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        scales: { x: { title: { display: true, text: 'เวลาปิด' },
                       ticks: { callback: v => _ctmHhmm(v) } } },
        plugins: { legend: { display: false },
                   tooltip: { callbacks: { label: c => 'ปิดเฉลี่ย ' + _ctmHhmm(c.parsed.x) } } } } });
  } else if (_ctmChartClose) { _ctmChartClose.destroy(); _ctmChartClose = null; }
}

// Minutes from midnight → HH:MM. Values past 1440 are after-midnight closes the
// endpoint pushed forward so 01:30 sorts after 23:00 — wrap them back for display.
function _ctmHhmm(v) {
  if (v == null) return '—';
  const m = Math.round(v);
  return String(Math.floor(m / 60) % 24).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
}

// ── Dive-in summaries for the Data Freshness charts ────────────────────
async function _diveCtmDays(_cfg) {
  const d = _ctmLast;
  const daily = (d && d.daily) || [];
  if (!daily.length) return '<p class="text-xs text-slate-400 p-2">ไม่มีข้อมูลรายวันในช่วงนี้</p>';
  const s = d.summary || {};
  const gaps = daily.filter(r => r.missing > 0);
  const worst = gaps.slice().sort((a, b) => b.missing - a.missing)[0];
  const bills = daily.map(r => r.bills);
  const billOut = _miOutlierSet(bills);
  const streak = (() => {                    // longest run of complete days
    let best = 0, cur = 0;
    daily.forEach(r => { cur = r.missing ? 0 : cur + 1; best = Math.max(best, cur); });
    return best;
  })();
  return _diveSection('ช่วงที่กำลังดู', '',
      _diveMetric('หน้าต่าง', `${esc(d.window_from || daily[0].day)} → ${esc(daily[daily.length - 1].day)}`) +
      _diveMetric('สาขาทั้งหมด', fmtN(s.branch_count || 0)) +
      _diveMetric('วันที่ข้อมูลครบทุกสาขา', fmtN(daily.length - gaps.length) + ' / ' + fmtN(daily.length),
                  gaps.length ? 'bad' : 'good') +
      _diveMetric('ข้อมูลล่าสุดทั้งระบบ', esc(d.data_max_date || '—'))) +
    _diveSection('ช่องว่างของข้อมูล', '',
      (gaps.length
        ? _diveBullet('⚠', `มี <b>${gaps.length}</b> วันที่ไม่ครบทุกสาขา — วันที่ขาดมากสุดคือ ` +
            `<b>${esc(worst.day)}</b> (ขาด ${worst.missing} สาขา) · ทุกตัวเลขในแดชบอร์ดของวันเหล่านี้ ` +
            `<b>ต่ำกว่าความจริง</b> ไม่ใช่ยอดตก`, 'bad')
        : _diveBullet('✓', 'ทุกวันในช่วงนี้มีข้อมูลครบทุกสาขา', 'good')) +
      _diveBullet('', `ช่วงที่ครบต่อเนื่องยาวสุด <b>${streak}</b> วัน`) +
      (gaps.length > 3
        ? _diveBullet('', `ขาดถี่ (${gaps.length} วัน) — น่าจะเป็นปัญหา sync ที่เกิดซ้ำ ไม่ใช่เหตุการณ์เดียว ` +
            `ตรวจตารางด้านล่างว่าสาขาเดิมหรือคนละสาขา`, 'warn')
        : '')) +
    _diveSection('ปริมาณบิลรายวัน', '',
      _diveBullet('', `เฉลี่ย <b>${fmtN(Math.round(bills.reduce((a, b) => a + b, 0) / bills.length))}</b> บิล/วัน ` +
        `· สูงสุด ${fmtN(Math.max(...bills))} · ต่ำสุด ${fmtN(Math.min(...bills))}`) +
      (billOut.size
        ? _diveBullet('', 'วันที่จำนวนบิลหลุดกรอบ: ' +
            [...billOut].map(i => `<b>${esc(daily[i].day)}</b> (${fmtN(daily[i].bills)})`).join(', ') +
            ' — เช็คก่อนว่าเป็นวันที่ข้อมูลไม่ครบหรือไม่ ก่อนสรุปว่าขายดี/ขายตก', 'warn')
        : _diveBullet('✓', 'ไม่พบวันที่จำนวนบิลหลุดกรอบ', 'good')));
}

async function _diveCtmClose(_cfg) {
  const d = _ctmLast;
  const brs = ((d && d.branches) || []).filter(r => r.avg_close_min != null);
  if (!brs.length) return '<p class="text-xs text-slate-400 p-2">ไม่มีข้อมูลเวลาปิดในช่วงนี้</p>';
  const sorted = brs.slice().sort((a, b) => b.avg_close_min - a.avg_close_min);
  const mins = brs.map(r => r.avg_close_min);
  const out = _miOutlierSet(mins);
  const avg = mins.reduce((a, b) => a + b, 0) / mins.length;
  const spread = Math.max(...mins) - Math.min(...mins);
  return _diveSection('เวลาปิดของเครือ', '',
      _diveMetric('ปิดเฉลี่ยทั้งเครือ', _ctmHhmm(avg)) +
      _diveMetric('ปิดดึกสุด', `${esc(sorted[0].branch)} · ${_ctmHhmm(sorted[0].avg_close_min)}`) +
      _diveMetric('ปิดเร็วสุด', `${esc(sorted[sorted.length - 1].branch)} · ${_ctmHhmm(sorted[sorted.length - 1].avg_close_min)}`) +
      _diveMetric('ช่วงห่างหัว–ท้าย', Math.round(spread) + ' นาที', spread > 90 ? 'bad' : '')) +
    _diveSection('อ่านยังไง', '▲',
      _diveBullet('', 'เวลานี้คือ<b>บิลสุดท้ายของวัน</b>เฉลี่ย 30 วันล่าสุด ไม่ใช่เวลาปิดตามป้าย — ' +
        'เวลาก่อน 06:00 ถูกนับเป็นปิดหลังเที่ยงคืน จึงเรียงถัดจาก 23:00 ไม่ใช่ขึ้นต้นแถว') +
      (out.size
        ? _diveBullet('⚠', 'สาขาที่เวลาปิดหลุดกรอบเทียบเพื่อน: ' +
            [...out].map(i => `<b>${esc(brs[i].branch)}</b> (${_ctmHhmm(brs[i].avg_close_min)})`).join(', ') +
            ' — ถ้าไม่ได้ตั้งใจให้ต่าง ให้เช็คว่าเป็นการปิดบิลช้า (ค้างบิลไว้) หรือเปิดจริงต่างจริง', 'warn')
        : _diveBullet('✓', 'ทุกสาขาปิดในกรอบเวลาใกล้เคียงกัน', 'good')) +
      (spread > 90
        ? _diveBullet('↔️', `หัวกับท้ายห่างกัน <b>${Math.round(spread)}</b> นาที — ค่าแรงและค่าไฟของสาขาที่ปิดดึก ` +
            `สูงกว่าเพื่อนโดยไม่จำเป็นหรือไม่ ควรดูคู่กับยอดขายช่วงชั่วโมงท้าย`, 'warn')
        : ''));
}

_DIVE_ENHANCERS.ctmChartDays = _diveCtmDays;
_DIVE_ENHANCERS.ctmChartClose = _diveCtmClose;

// ── Industry & Economy context (curated figures + live news strip) ──
async function loadMarket() {
  try {
    const m = await api(`${API}/api/market`);
    const trIco = t => t === 'up' ? '▲' : t === 'down' ? '▼' : '▬';
    const trCol = t => t === 'up' ? '#10b981' : t === 'down' ? '#ef4444' : '#64748b';

    document.getElementById('market-asof').textContent = `ข้อมูล ณ ${m.as_of || '—'}`;

    // Live FX / gold / oil — daily change colored as a cost lens (rising fx/oil/gold =
    // rising import/energy/inflation pressure → amber; easing → green). Sub line carries
    // the meaning so the arrow isn't a blunt good/bad judgement.
    const qEl = document.getElementById('market-quotes');
    if (qEl) {
      const q = m.quotes || [];
      if (q.length) {
        qEl.innerHTML = q.map(x => {
          const c = x.change_pct;
          const arrow = c == null ? '' : c > 0 ? '▲' : c < 0 ? '▼' : '▬';
          const col = c == null ? 'var(--text-muted)' : c > 0 ? '#d97706' : c < 0 ? 'var(--good-ink)' : 'var(--text-muted)';
          return `<div class="rounded-lg p-3" style="background:var(--bg-subtle);border:1px solid var(--border-soft)">
            <p class="text-[10px] font-semibold uppercase tracking-wide" style="color:var(--text-muted)">${esc(x.label)}</p>
            <p class="text-lg font-bold mt-0.5" style="color:var(--text-primary)">${esc(x.value)}${c != null ? ` <span style="font-size:11px;color:${textInk(col)}">${arrow} ${Math.abs(c).toFixed(1)}%</span>` : ''}</p>
            <p class="text-[10.5px]" style="color:var(--text-secondary)">${esc(x.sub || '')}</p>
          </div>`;
        }).join('');
        const qa = document.getElementById('market-quotes-asof');
        if (qa) qa.textContent = m.quotes_status === 'ok' ? 'อัปเดตอัตโนมัติ ทุก 30 นาที' : m.quotes_status === 'stale' ? '⚠ อาจล่าช้า' : '';
      } else {
        qEl.innerHTML = `<p class="text-xs italic col-span-2 md:col-span-4" style="color:var(--text-muted)">ราคาตลาดสด ไม่พร้อมใช้งานขณะนี้</p>`;
      }
    }

    document.getElementById('market-macro').innerHTML = (m.macro || []).map(x => `
      <div class="rounded-lg p-3" style="background:var(--bg-subtle);border:1px solid var(--border-soft)">
        <p class="text-[10px] font-semibold uppercase tracking-wide" style="color:var(--text-muted)">${esc(x.label)}</p>
        <p class="text-lg font-bold mt-0.5" style="color:var(--text-primary)">${esc(x.value)} <span style="font-size:11px;color:${textInk(trCol(x.trend))}">${trIco(x.trend)}</span></p>
        <p class="text-[10.5px]" style="color:var(--text-secondary)">${esc(x.sub)}</p>
        <p class="text-[9.5px] mt-1" style="color:var(--text-muted)">${esc(x.year)} · ${esc(x.source)}</p>
      </div>`).join('');

    document.getElementById('market-industry').innerHTML = (m.industry || []).map(x => `
      <div class="flex items-start justify-between gap-3 p-2.5 rounded-lg" style="background:var(--bg-subtle)">
        <div>
          <p class="text-xs font-semibold" style="color:var(--text-primary)">${esc(x.label)}</p>
          <p class="text-[10.5px]" style="color:var(--text-secondary)">${esc(x.sub)} · <span style="color:var(--text-muted)">${esc(x.source)}</span></p>
        </div>
        <p class="text-sm font-bold whitespace-nowrap" style="color:var(--indigo)">${esc(x.value)}</p>
      </div>`).join('');

    renderMarketBenchmark(m.benchmark);

    const ns = document.getElementById('market-news');
    if ((m.news || []).length) {
      ns.innerHTML = m.news.map(n => `
        <a href="${esc(n.url)}" target="_blank" rel="noopener" class="block p-2 rounded-lg" style="border:1px solid var(--border-soft)">
          <p class="text-xs font-medium" style="color:var(--text-primary)">${esc(n.title)}</p>
          <p class="text-[10px]" style="color:var(--text-muted)">${esc(n.source || '')}${n.published ? ' · ' + esc(String(n.published).slice(0, 16)) : ''}</p>
        </a>`).join('')
        + (m.news_status === 'stale' ? `<p class="text-[10px] italic mt-1" style="color:var(--text-muted)">⚠ headlines may be delayed (feed slow)</p>` : '');
    } else {
      ns.innerHTML = `<p class="text-xs italic" style="color:var(--text-muted)">ข่าวสด ไม่พร้อมใช้งานขณะนี้ — ข้อมูลตลาดด้านบนยังเป็นปัจจุบัน</p>`;
    }

    document.getElementById('market-sources').innerHTML =
      'แหล่งข้อมูล: ' + (m.sources || []).map(s =>
        `<a href="${esc(s.url)}" target="_blank" rel="noopener" style="color:var(--text-secondary);text-decoration:underline">${esc(s.name)}</a>`
      ).join(' · ');
  } catch(e) {
    const a = document.getElementById('market-asof');
    if (a) a.textContent = 'ข้อมูลตลาดยังไม่พร้อมใช้งาน';
  }
}

// Benchmark the chain's revenue against industry growth. Prefer a fair YoY
// comparison; if <12 months of history, show MoM neutrally (no verdict) so we
// never compare a monthly delta against an annual industry figure.
async function renderMarketBenchmark(b) {
  const el = document.getElementById('market-benchmark');
  if (!el || !b) { if (el) el.innerHTML = ''; return; }
  let ourPct = null, label = '', isYoY = false;
  try {
    const rows = await api(`${API}/api/sales/monthly`);
    const arr = Array.isArray(rows) ? rows : (rows.monthly || rows.data || []);
    const byMonth = {};
    arr.forEach(r => { const k = r.period_month || r.month; if (k) byMonth[k] = r.revenue_ex_vat || r.revenue || 0; });
    const months = Object.keys(byMonth).sort();
    if (months.length) {
      // Guard partial months (e.g. the first ~May-2025 month is incomplete): a
      // month is only "reliable" if its revenue is ≥ 50% of the series median.
      const vals = months.map(k => byMonth[k]).filter(v => v > 0).sort((a, b) => a - b);
      const median = vals.length ? vals[Math.floor(vals.length / 2)] : 0;
      const reliable = k => byMonth[k] > 0 && (median === 0 || byMonth[k] >= 0.5 * median);
      const rel = months.filter(reliable);
      if (rel.length) {
        const last = rel[rel.length - 1];
        const [y, mo] = last.split('-');
        const prevY = `${(+y - 1)}-${mo}`;
        if (reliable(prevY)) {                       // fair YoY only vs a full prior-year month
          ourPct = (byMonth[last] - byMonth[prevY]) / byMonth[prevY] * 100;
          label = `รายได้ ${last} เทียบปีก่อน (YoY)`; isYoY = true;
        } else if (rel.length >= 2) {                 // else MoM vs prior reliable month
          const prev = rel[rel.length - 2];
          ourPct = (byMonth[last] - byMonth[prev]) / byMonth[prev] * 100;
          label = `รายได้ ${last} เทียบเดือนก่อน`;
        }
      }
    }
  } catch(e) { /* fall through to neutral */ }

  const ind = b.industry_growth_pct;
  if (ourPct == null) {
    el.innerHTML = `<div class="p-3 rounded-lg" style="background:rgba(31,58,107,0.06);border:1px solid var(--indigo)">
      <p class="text-xs" style="color:var(--text-secondary)">Benchmark ตลาด: ${esc(b.label)}</p></div>`;
    return;
  }
  const pctColor = ourPct >= 0 ? '#10b981' : '#ef4444';
  if (isYoY) {
    const out = ourPct >= ind;
    const accent = out ? '#10b981' : '#f59e0b';
    el.innerHTML = `<div class="p-3 rounded-lg" style="background:${out ? 'rgba(16,185,129,0.07)' : 'rgba(245,158,11,0.07)'};border:1px solid ${accent}">
      <p class="text-xs font-semibold" style="color:var(--text-primary)">เทียบกับตลาด ${out ? '— เหนือกว่าตลาด ✓' : '— ต่ำกว่าตลาด ⚠'}</p>
      <p class="text-[11px] mt-0.5" style="color:var(--text-secondary)">${esc(label)}: <b style="color:${textInk(pctColor)}">${ourPct >= 0 ? '+' : ''}${ourPct.toFixed(1)}%</b> · ตลาดร้านอาหารไทย ~+${ind}%/ปี</p>
    </div>`;
  } else {
    el.innerHTML = `<div class="p-3 rounded-lg" style="background:rgba(31,58,107,0.06);border:1px solid var(--indigo)">
      <p class="text-xs font-semibold" style="color:var(--text-primary)">ผลของเรา vs ตลาด</p>
      <p class="text-[11px] mt-0.5" style="color:var(--text-secondary)">${esc(label)}: <b style="color:${textInk(pctColor)}">${ourPct >= 0 ? '+' : ''}${ourPct.toFixed(1)}%</b> · ตลาด ~+${ind}%/ปี <span style="color:var(--text-muted)">(ยังไม่ครบ 12 เดือนสำหรับ YoY)</span></p>
    </div>`;
  }
}



// ── Scheduled-task health (admins): what the office PC's watchdog last reported ──
// backend/task_watchdog.py POSTs every run; /api/usage/task-health adds how long ago
// that was. Shown only when a task is red/late, or when the PC itself has gone quiet
// (no report for 36 h = nothing is syncing). ✕ hides THIS problem set for today only —
// it comes back tomorrow, because 15 silent red days (Push Invisible, 2026-09) is the
// thing this exists to prevent.
async function renderTaskHealthBanner() {
  if (!window.AUTH || !window.AUTH.enabled || !hasModule('system.admin')) return;
  let d = null;
  try { d = await api(`${API}/api/usage/task-health`); } catch(e) { return; }
  const old = document.getElementById('homeTaskHealthBanner');
  const bad = (d && d.problems) || [];
  if (!d || !d.known || (!d.silent && !bad.length)) { if (old) old.remove(); return; }
  const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Bangkok' });
  const key = ('taskHealth:' + today + ':' + (d.silent ? 'silent' : d.signature)).replace(/[^\w:|=. ()-]/g, '_');   // safe inside the inline onclick
  try { if (localStorage.getItem(key)) { if (old) old.remove(); return; } } catch(e) {}
  const host = document.getElementById('tab-home'); if (!host) return;
  let b = old;
  if (!b) { b = document.createElement('div'); b.id = 'homeTaskHealthBanner'; host.insertBefore(b, host.firstChild); }
  const red = d.silent || bad.some(t => t.level === 'fail' || t.level === 'stale' || t.level === 'stuck');
  b.className = 'rounded-xl px-4 py-3 mb-4 text-sm flex items-start justify-between gap-3 on-light';   // light fill set via cssText: the hex-list scope can't see rgb()
  b.style.cssText = red ? 'background:#fef2f2;border:1px solid #fca5a5;color:#7f1d1d' : 'background:#fffbeb;border:1px solid #fcd34d;color:#78350f';
  const TH = { fail: 'ล้มเหลว', stale: 'ไม่ได้รันหลายวัน', stuck: 'ค้าง', missed: 'ไม่ได้รันรอบล่าสุด' };
  const when = s => s ? esc(String(s).replace('T', ' ').slice(5, 16)) : '—';
  const rows = bad.map(t => `<li><b>${esc(String(t.name).replace(/^Oshinei /, ''))}</b> — ${TH[t.level] || esc(t.level)}${t.days ? ` <b>${t.days} วันแล้ว</b>` : ''} · รันล่าสุด ${when(t.last_run)}${t.log_line ? `<br><span class="text-[11px]" style="opacity:.85;font-family:ui-monospace,monospace">${esc(t.log_line)}</span>` : ` · <span class="text-[11px]" style="opacity:.85">${esc(t.meaning || '')}</span>`}</li>`).join('');
  const cloud = d.source === 'cloud' || d.source === 'both';   // cloud_jobs.py's watchdog reports too (source set only then)
  const where = d.source === 'cloud' ? 'บนคลาวด์' : (d.source === 'both' ? 'บนคลาวด์และเครื่องสำนักงาน' : 'บนเครื่องสำนักงาน');
  const head = d.silent
    ? (cloud
      ? `<b>ตัวตรวจงานบนคลาวด์ไม่ได้รายงานมา ${Math.round(d.age_hours)} ชม.</b> — ตรวจที่ ตั้งค่า > งานอัตโนมัติ <span class="text-[11px]" style="opacity:.8">(รายงานล่าสุด ${when(d.received_at)})</span>`
      : `<b>เครื่องสำนักงานไม่ได้รายงานมา ${Math.round(d.age_hours)} ชม.</b> — ถ้าเครื่องปิดอยู่ งาน sync / backup / ดึงรีวิว จะไม่รันเลย <span class="text-[11px]" style="opacity:.8">(รายงานล่าสุด ${when(d.received_at)} จาก ${esc(d.host || '?')})</span>`)
    : `<b>งานอัตโนมัติ${where}มีปัญหา ${bad.length} งาน</b> <span class="text-[11px]" style="opacity:.8">(ตรวจล่าสุด ${when(d.checked_at)} · ${esc(d.host || '?')})</span>`;
  b.innerHTML = `<div>${head}${rows ? `<ul class="mt-1 ml-4 list-disc space-y-0.5">${rows}</ul>` : ''}</div>
    <button onclick="(function(){try{localStorage.setItem('${key}','1')}catch(e){};var el=document.getElementById('homeTaskHealthBanner');if(el)el.remove()})()" class="text-xs" style="color:inherit" title="ซ่อนวันนี้ (พรุ่งนี้กลับมาถ้ายังไม่หาย)">✕</button>`;
}

// ── Weekly adoption banner (admins): this week's digest from /api/usage/adoption-digest ──
async function renderAdoptionBanner() {
  if (!window.AUTH || !window.AUTH.enabled || !hasModule('system.admin')) return;
  let d = null;
  try { d = await api(`${API}/api/usage/adoption-digest`); } catch(e) { return; }
  const rec = d && d.latest;
  if (!rec || rec.week !== d.week) return;
  const key = 'adoptionBanner:' + rec.week;
  try { if (localStorage.getItem(key)) return; } catch(e) {}
  const host = document.getElementById('tab-home'); if (!host) return;
  let b = document.getElementById('homeAdoptionBanner');
  if (!b) { b = document.createElement('div'); b.id = 'homeAdoptionBanner'; host.insertBefore(b, host.firstChild); }
  b.className = 'rounded-xl px-4 py-3 mb-4 text-sm flex items-start justify-between gap-3 on-light';   // light fill set via cssText: the hex-list scope can't see rgb()
  b.style.cssText = 'background:#fffbeb;border:1px solid #fcd34d;color:#78350f';
  const never = rec.never || [], away = rec.away || [];
  b.innerHTML = `<div><b>สรุปการเข้าใช้สัปดาห์ ${esc(fmtWeekTh(rec.week))}</b> — ยังไม่เคยเข้าใช้ <b>${never.length}</b> คน${never.length ? ' (' + never.slice(0, 6).map(esc).join(', ') + (never.length > 6 ? ' …' : '') + ')' : ''} · หายไปเกิน 14 วัน <b>${away.length}</b> คน${away.length ? ' (' + away.slice(0, 6).map(esc).join(', ') + ')' : ''}${rec.sent ? ' · ส่ง Telegram แล้ว' : ' · ยังไม่ได้ตั้ง Telegram'} — <a href="#" onclick="switchTab('settings');switchSettingsTab('usage');return false" class="underline">ดูรายชื่อ</a></div>
    <button onclick="(function(){try{localStorage.setItem('${key}','1')}catch(e){};var el=document.getElementById('homeAdoptionBanner');if(el)el.remove()})()" class="text-xs" style="color:var(--amber-ink)" title="ซ่อนสัปดาห์นี้">✕</button>`;
}
