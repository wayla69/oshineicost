// ─── 03-tabs-main.js — slice 3/9 of the former app.js (P2 structure refactor) ───
// Sequential load order matters: index.html loads 01→09; classic scripts
// share one global scope, so this equals the original single file.

// ════════════════════════════════════════════════════════════════
// TAB 5 — OPERATIONS
// ════════════════════════════════════════════════════════════════
function switchOpsSubtab(name) {
  activeOpsSubtab = name;
  const subs = ['cohort','affinity'];   // 'whatif' moved to the Simulators hub
  document.querySelectorAll('#tab-ops .subtab-btn').forEach((b, i) => {
    b.classList.toggle('active', subs[i] === name);
  });
  subs.forEach(s => {
    const el = document.getElementById('opsub-' + s);
    if (el) el.classList.toggle('hidden', s !== name);
  });
  loadOpsSubtab();
}

function loadOpsSubtab() {
  if (activeOpsSubtab === 'cohort')    loadCohort();
  if (activeOpsSubtab === 'affinity')  loadAffinity();
  if (activeOpsSubtab === 'ai')        renderAIBlankState();
}

async function loadTimeAnalysis() {
  const _fk = _filterKey() + '|' + _timeGranularity;
  if (_tabLastKey.opsTime === _fk) return;
  _tabLastKey.opsTime = _fk;

  // Build extra param for granularity
  const gran = `&granularity=${_timeGranularity}`;

  // Update subtitles based on granularity
  const granLabel = { daily: 'day', weekly: 'week', monthly: 'month' }[_timeGranularity] || 'day';
  const hs = document.getElementById('timeHourlySubtitle');
  const ds = document.getElementById('timeDowSubtitle');
  if (hs) hs.textContent = `รายได้เฉลี่ยต่อ ${granLabel} · grey bars = closed hours`;
  if (ds) ds.textContent = `รายได้เฉลี่ยต่อ ${granLabel} ของสัปดาห์`;

  // Use hourly_open which annotates each hour with is_open from closed_hours config
  const [hourly, dow] = await Promise.all([
    api(`${API}/api/analysis/hourly_open${qp()}${gran}`),
    api(`${API}/api/analysis/dow${qp()}${gran}`),
  ]);
  // Filter to operating hours only by default — user can toggle to see all
  const opOnly = document.getElementById('hourlyOpOnlyToggle')?.checked !== false;
  const filtered = opOnly ? hourly.filter(h => h.is_open !== false) : hourly;
  renderTimeKpis(filtered);
  renderHourlyChart(hourly);  // pass full so we can grey closed bars
  renderDowTimeChart(dow);
  renderTimeHeatmap(filtered);
}

function renderTimeKpis(hourly) {
  const el = document.getElementById('timeSegmentKpis');
  if (!el) return;
  const segments = [
    { label: 'เช้า',   emoji: '', hours: [6,7,8,9,10],          color: '#f59e0b' },
    { label: 'กลางวัน',     emoji: '', hours: [11,12,13],             color: '#10b981' },
    { label: 'เย็น',    emoji: '', hours: [17,18,19,20,21],       color: '#4a70b0' },
    { label: 'ดึก',emoji: '', hours: [22,23,0,1,2],          color: '#7a66b8' },
  ];
  const total = hourly.reduce((s, r) => s + (r.avg_daily_revenue ?? r.revenue ?? 0), 0);
  el.innerHTML = segments.map(seg => {
    const rev = seg.hours.reduce((s, h) => s + (hourly.find(r => r.hour === h)?.avg_daily_revenue ?? hourly.find(r => r.hour === h)?.revenue ?? 0), 0);
    const pct = total ? (rev / total * 100).toFixed(1) : '0.0';
    const peakHour = seg.hours.reduce((mx, h) => {
      const r = hourly.find(r2 => r2.hour === h)?.revenue || 0;
      return r > (hourly.find(r2 => r2.hour === mx)?.revenue || 0) ? h : mx;
    }, seg.hours[0]);
    return `<div class="card kpi-card p-4">
      <div class="kpi-card-top"><p class="kpi-label">${seg.emoji} ${seg.label}</p></div>
      <p class="kpi-value" style="color:${textInk(seg.color)}">${fmt(rev)}</p>
      <div class="kpi-card-bottom">
        <p class="kpi-sub">${pct}% of ${{ daily:'day', weekly:'week', monthly:'month' }[_timeGranularity]||'day'} · peak ${peakHour}:00</p>
      </div>
    </div>`;
  }).join('');
}

function renderHourlyChart(hourly) {
  const labels = hourly.map(r => `${r.hour}:00`);
  const revenues = hourly.map(r => r.avg_daily_revenue ?? r.revenue ?? 0);
  const maxRev = Math.max(...revenues);
  const colors = hourly.map(r => {
    // Hours marked closed in operating-hours config get distinct grey treatment
    if (r.is_open === false) return '#cbd5e1';
    const base = colorForHour(r.hour);
    const v = r.revenue || 0;
    if (maxRev === 0) return base + '40';
    const intensity = v / maxRev;
    if (intensity < 0.1) return base + '33';
    if (intensity < 0.3) return base + '66';
    if (intensity < 0.6) return base + 'aa';
    return base;
  });
  mkChart('chartHourly', {
    type: 'bar',
    data: { labels, datasets: [{ data: revenues, backgroundColor: colors, borderRadius: 3, borderWidth: 0 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => {
          const r = hourly[ctx.dataIndex];
          const closedTag = r.is_open === false ? ' ⊘ closed' : '';
          const gLabel = { daily: 'day', weekly: 'week', monthly: 'month' }[_timeGranularity] || 'day';
          return ` ${fmtN(ctx.raw)} avg/${gLabel} · ${r.tx_count} tx total${closedTag}`;
        } } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: '#94a3b8', font: { size: 9 }, maxRotation: 0,
          callback: (v, i) => i % 3 === 0 ? `${i}:00` : '' } },
        y: { grid: { color: '#f1f5f9' }, ticks: { color: '#94a3b8', font: { size: 10 },
          callback: v => fmt(v) } },
      },
    },
  });
}

function renderDowTimeChart(dow) {
  if (!dow || !dow.length) return;
  const dowOrder = [1,2,3,4,5,6,0]; // Mon..Sun
  const shortDays = ['จ','อ','พ','พฤ','ศ','ส','อา'];
  const sorted = dowOrder.map(d => dow.find(r => r.dow === d) || { dow: d, revenue: 0, avg_revenue: 0, tx_count: 0 });
  const revenues = sorted.map(r => r.avg_revenue ?? r.revenue ?? 0);
  const maxRev = Math.max(...revenues);
  const colors = sorted.map((r) => DOW_COLORS[r.dow] || SEMANTIC.revenue);
  mkChart('chartDowTime', {
    type: 'bar',
    data: {
      labels: shortDays,
      datasets: [{ data: revenues, backgroundColor: colors, borderRadius: 4, borderWidth: 0 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        datalabels: {
          display: true, anchor: 'end', align: 'top',
          formatter: v => fmt(v),
          color: '#1e293b', font: { size: 10, weight: '600' },
        },
        tooltip: { callbacks: { label: ctx => { const d = sorted[ctx.dataIndex]; const gLabel = { daily:'day', weekly:'week', monthly:'month' }[_timeGranularity]||'day'; return ` ${fmtN(ctx.raw)} avg/${gLabel} · ${d?.day_count??0} ${d?.label??''}s · ${d?.tx_count?.toLocaleString()} tx total`; } } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: '#64748b', font: { size: 12, weight: '600' } } },
        y: { display: false },
      },
      layout: { padding: { top: 24 } },
    },
  });
}

function renderTimeHeatmap(hourly) {
  const el = document.getElementById('timeHeatmap');
  if (!el) return;
  const maxRev = Math.max(...hourly.map(r => r.revenue || 0));
  const getColor = (rev) => {
    if (!rev || maxRev === 0) return 'var(--bg-subtle)';
    return heatSeq(rev / maxRev);  // darker = busier (volume is not good/bad)
  };
  el.innerHTML = hourly.map(r => `
    <div class="flex flex-col items-center" style="width:calc((100% - ${23*4}px) / 24);min-width:28px">
      <div class="w-full rounded" style="height:40px;background:${getColor(r.revenue)};cursor:default"
        title="${r.hour}:00 — ${fmtN(r.revenue)} · ${r.tx_count} tx"></div>
      <span class="text-xs text-slate-400 mt-1">${r.hour % 3 === 0 ? r.hour+':00' : ''}</span>
    </div>`).join('');
}





// ── Connection health ──────────────────────────────────────────
let _healthTimer = null;

async function checkApiHealth() {
  const badge = document.getElementById('dbStatus');
  const label = document.getElementById('dbStatusText');
  if (!badge) return true;
  // a hidden app (another tab, a phone in a pocket) keeps its last verdict instead of polling
  if (document.hidden && !badge.classList.contains('checking')) return !badge.classList.contains('offline');

  // AbortController-based timeout (compatible with all modern browsers)
  const controller = new AbortController();
  const tid = setTimeout(() => controller.abort(), 5000);

  try {
    const r = await fetch(`${API}/api/months`, { signal: controller.signal });
    clearTimeout(tid);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    badge.className = 'db-status online';
    label.textContent = 'DB Connected';
    return true;
  } catch(e) {
    clearTimeout(tid);
    // Only show offline if it's a real network failure (not just a timeout that resolved)
    if (e && e.name === 'AbortError') {
      badge.className = 'db-status offline';
      label.textContent = 'Timeout';
    } else if (e && (e.message || '').includes('Failed to fetch') || e instanceof TypeError) {
      badge.className = 'db-status offline';
      label.textContent = 'DB Offline';
    } else {
      // API responded with an error status, but server IS reachable
      badge.className = 'db-status online';
      label.textContent = 'DB Connected';
      return true;
    }
    return false;
  }
}

function showOfflineOverlay() {
  document.getElementById('offlineOverlay').style.display = 'flex';
}
function hideOfflineOverlay() {
  document.getElementById('offlineOverlay').style.display = 'none';
}

async function retryConnection() {
  const badge = document.getElementById('dbStatus');
  const label = document.getElementById('dbStatusText');
  if (badge) { badge.className = 'db-status checking'; label.textContent = 'Connecting…'; }
  const ok = await checkApiHealth();
  if (ok) {
    hideOfflineOverlay();
    clearInterval(_healthTimer);
    _healthTimer = setInterval(checkApiHealth, 30000);
    await Promise.all([loadBranches(), loadMonths()]);
    populateTargetMonths();
    initCostCategories();
    renderUserPresets();
    gPeriodWrite(); gPeriodRender();
    loadActiveTab();
  }
}

// ── Tab loading bar ────────────────────────────────────────────
function showLoadingBar() {
  const b = document.getElementById('loadingBar');
  if (b) b.style.display = 'block';
}
function hideLoadingBar() {
  const b = document.getElementById('loadingBar');
  if (b) b.style.display = 'none';
}

// ── Phone-first boot. On a small screen the owner's summary (#mobileSummary) is the
// landing page. The desktop shell behind it — 48 tab fragments (570 KB) and the Daily
// Brief's ~20 calls — used to boot FIRST, and the summary waited for it. Now the
// summary loads straight away and the shell boots on "เต็ม →" (ensureDesktopBooted).
// The branch login is force-desktop from applyPermissions, so it boots the shell at once.
function _phoneFirst() {
  try {
    if (!(window.innerWidth > 0)) return false;   // a hidden pane reports 0 wide — that is not a phone
    return window.matchMedia('(max-width:700px)').matches && !document.body.classList.contains('force-desktop');
  } catch (e) { return false; }
}
// The viewport can change after boot (a window dragged narrower or wider, a pane shown
// late): boot whichever side is now on screen and has not loaded yet.
let _appStarted = false, _resizeT = null;
function _viewportChanged() {
  clearTimeout(_resizeT);
  _resizeT = setTimeout(() => {
    if (!_appStarted) return;
    if (phOn() || _phoneApp()) { phViewportChanged(); return; }    // PR-D3: the phone shell comes and goes with the width (js/09c-mobile.js)
    if (_phoneFirst()) { if (!_msRpt) loadMobileSummary(); }
    else if (!_desktopBooted) ensureDesktopBooted();
  }, 250);
}
window.addEventListener('resize', _viewportChanged);
try { window.matchMedia('(max-width:700px)').addEventListener('change', _viewportChanged); } catch (e) {}
// The desktop shell: the fragments, the settings/preset furniture, the YTD period and
// the landing tab. One promise, shared by every caller; a no-op once booted.
let _desktopBooted = false, _desktopBootP = null;
// opts.noLanding: the caller opens its own page next (the phone shell, PR-D3), so no landing page is loaded first.
function ensureDesktopBooted(opts) {
  if (!_desktopBootP) _desktopBootP = (async () => {
    await ensureSlicesLoaded();               // the tabs' code (01-core.js) — a phone fetches it only now
    const lazy = !document.getElementById('tabsRoot')?.childElementCount;
    await loadTabFragments();
    // loadMonths() ran before the fragments existed on a phone: the pickers inside them (แนวโน้ม: cmpMonth / cmpVs) were never filled
    if (allMonths.length) { try { populateCmpDropdowns(); } catch (e) { console.warn('cmp pickers:', e); } }
    // on a phone the permissions ran before the fragments existed — the Settings →
    // ผู้ใช้งาน sub-tab lives in one, so apply them again now
    if (lazy && window.AUTH && window.AUTH.enabled) applyPermissions();
    populateTargetMonths();
    try { await initCostCategories(); } catch(e) { console.warn('initCostCategories:', e); }
    try { renderUserPresets(); } catch(e) { console.warn('renderUserPresets:', e); }
    // PR-PP1: the period is the ONE period bar's (PERIOD_DEFAULT_KIND to date - the month since PP3 - unless this device chose another)
    gPeriodWrite(); gPeriodRender();
    _desktopBooted = true;
    // PR-D1: the sidebar's department entries (js/18-launcher.js), then land: the department launcher on a desktop, the Daily Brief
    // (home) on a phone that opened the full dashboard (switchTab also hides the period controls home does not use).
    try { renderDeptNav(); } catch (e) { console.warn('renderDeptNav:', e); }
    if (!(opts && opts.noLanding)) switchTab(_landingTab());
    // a supply reminder was tapped (batch 3): open the page it names
    if (_supplyPendingLink) { const l = _supplyPendingLink; _supplyPendingLink = ''; try { supplyDeepLink(l); } catch (e) { console.warn('deep link:', e); } }
    // PR-F4: a franchise-message notification was tapped: head office's inbox (ร้านของฉัน is the owner's landing page already)
    if (_fmsgPending && _fmsgPending.go === 'fmsg' && !(opts && opts.noLanding) && tabAllowed('fmsg')) switchTab('fmsg');
  })();
  return _desktopBootP;
}

// ── Foreground. A phone comes back to the app hours later with the numbers it left:
// refresh the summary (or the open tab) when they are older than 5 min, re-check the
// API, and pause every infinite animation while the app is hidden (body.app-hidden).
const _STALE_MS = 5 * 60 * 1000;
document.addEventListener('visibilitychange', () => {
  document.body.classList.toggle('app-hidden', document.hidden);
  if (document.hidden) return;
  try { checkApiHealth(); } catch (e) {}
  try { appUpdateCheck(); } catch (e) {}                       // a deploy since this page loaded: the new version (js/01-core.js)
  let phone = false;
  try { phone = window.matchMedia('(max-width:700px)').matches; } catch (e) {}
  if (!phone) return;
  if (phOn()) { phForeground(); return; }                      // PR-D3: the phone shell refreshes what is on screen (js/09c-mobile.js)
  if (_phoneFirst()) { if (_msFetchedAt && Date.now() - _msFetchedAt > _STALE_MS) msRefresh(); }
  else if (_desktopBooted && _lastTabLoadAt && Date.now() - _lastTabLoadAt > _STALE_MS) loadActiveTab();
});

// ── Init ───────────────────────────────────────────────────────
async function init() {
  // PR-D3: the phone no longer boots the full shell because เต็ม → was used last (oshinei_phone_mode): every phone opens on the department
  // launcher (phBoot, js/09c-mobile.js) and the shell boots on the first page a person opens from it
  // the tabs' code and the 48 panels, before anything looks for them — a phone defers
  // both to ensureDesktopBooted
  if (!_phoneFirst()) { await ensureSlicesLoaded(); await loadTabFragments(); }
  initDarkMode();
  initTopbarFold();           // phone: the top bar's controls fold away (09c-mobile.js)
  showLoadingBar();
  await bootstrapAuth();
  if (window.AUTH.enabled && !window.AUTH.authenticated) {
    hideLoadingBar();
    showLogin();
    return;                       // hold the app until the user logs in
  }
  await loadBcMode();             // PR-S1: the system mode, before the launcher, the rail and the pages are drawn
  applyPermissions();
  await startApp();
}

async function startApp() {
  showLoadingBar();
  if (_supplyPendingLink) document.body.classList.add('force-desktop');    // a supply reminder opens its page, not the owner's phone summary
  try {
    const ok = await checkApiHealth();
    if (!ok) {
      hideLoadingBar();
      showOfflineOverlay();
      _healthTimer = setInterval(async () => {
        const alive = await checkApiHealth();
        if (alive) {
          hideOfflineOverlay();
          clearInterval(_healthTimer);
          _healthTimer = setInterval(checkApiHealth, 30000);
          await Promise.all([loadBranches(), loadMonths()]);
          _appStarted = true;
          if (_phoneApp()) await phBoot(); else if (_phoneFirst()) loadMobileSummary(); else await ensureDesktopBooted();
        }
      }, 5000);
      return;
    }
    await Promise.all([loadBranches(), loadMonths()]);
    _appStarted = true;
    if (_phoneApp()) {
      // PR-D3: a phone opens on the department launcher; the owner's summary and the shell load when a person opens them (phBoot, js/09c-mobile.js)
      await phBoot();
      initDataCoverageNote();
    } else if (_phoneFirst()) {
      // the owner's summary first — one management payload; the desktop shell
      // boots when "เต็ม →" is tapped (ensureDesktopBooted)
      await initDataCoverageNote();   // never rejects; the phone's MTD marker needs its date
      loadMobileSummary();
    } else {
      const deepLinked = !!_supplyPendingLink;
      await ensureDesktopBooted();
      // QA M4 · on a phone a supply role starts on หน้างาน, not on the Daily Brief the desktop shell opens with
      try { if (!deepLinked && window.matchMedia('(max-width:700px)').matches && _supplyPhoneRole() && _phoneModeSaved() !== 'summary') supplyOpen(); } catch (e) { console.warn('supply landing:', e); }
      initDataCoverageNote();
      // First login of a person: the role's three tabs, on top of the home page
      try { await maybeShowStartCard(); } catch(e) { console.warn('start card:', e); }
    }
    // Periodic health check every 30 s (silent badge update only; idle while hidden)
    _healthTimer = setInterval(checkApiHealth, 30000);
  } catch(err) {
    hideLoadingBar();
    // Only show offline overlay for network errors; other errors are shown by the error banner
    if (err instanceof TypeError || (err.name === 'AbortError') || String(err).includes('fetch')) {
      showOfflineOverlay();
    }
    console.error('Init failed:', err);
  } finally {
    hideLoadingBar();
  }
}

// Show "ข้อมูลถึง <date>" in the top bar, with a warning tint when the latest
// month is missing its final day (known upstream Easy-export off-by-one).
async function initDataCoverageNote() {
  const el = document.getElementById('dataCoverageNote');
  if (!el) return;
  try {
    const c = await api(`${API}/api/data-coverage`);
    if (!c || !c.last_date) return;
    // The chart glance layer (js/01-core.js) reads this to tell a month still in
    // progress (drawn hollow, tagged MTD); charts drawn before it arrived re-check.
    if (window.__dataLastDate !== c.last_date) {
      window.__dataLastDate = c.last_date;
      try { gPeriodWrite(); gPeriodRender(); } catch (e) { /* the bar is not drawn yet */ }   // PR-PP1: the anchor is the last data day
      Object.values(chartInstances).forEach(ch => { try { if (ch && ch.$gi && ch.$gi.grain) ch.update('none'); } catch (e) { /* stale chart */ } });
    }
    const TH_MON = ['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
    const [y, m, d] = c.last_date.split('-').map(Number);
    const human = `${d} ${TH_MON[m-1]} ${y+543}`;   // พ.ศ.
    // PR-MA: on the phone top bar the chip is short ("ถึง 22 ต.ค."): the words in .dcn-x are hidden there (index.html, PHONE DISPLAY MODES)
    const chip = `<span class="dcn-x">ข้อมูล</span>ถึง ${d} ${TH_MON[m-1]}<span class="dcn-x"> ${y+543}</span>`;
    if (c.last_day_missing) {
      el.innerHTML = `⚠ ${chip}`;
      el.style.background = '#fef3c7';
      el.style.color = '#92400e';   // fixed light badge (#fef3c7) in both themes: 6.4:1
      el.setAttribute('title', `ข้อมูลล่าสุดถึง ${human} เท่านั้น — การ export จาก Easy POS ตัดวันสุดท้ายของเดือนออก `
               + `ยอดรวมของเดือนล่าสุดจึงยังไม่ครบ (ขาดวันสิ้นเดือน). อัตราส่วน เช่น ต้นทุนอาหาร % กระทบน้อย. `
               + `แก้โดย re-export เดือนนั้นแบบรวมวันสิ้นเดือน แล้วอัปโหลดใหม่.`);   // a tooltip
    } else {
      el.innerHTML = chip;
      el.style.background = 'var(--bg-subtle)';
      el.style.color = 'var(--text-secondary)';
      el.title = `ข้อมูลล่าสุดถึง ${human}`;
    }
    el.style.display = '';
  } catch (e) { /* note is non-critical */ }
}

async function loadBranches() {
  if (isPinnedSession()) return;       // a pinned login never reads the branch list (its filter is a hidden field)
  try {
    const data = await api(`${API}/api/branches`);
    const sel = document.getElementById('selBranch');
    const prev = sel.value;
    // Keep only the "All Branches" placeholder, rebuild the rest
    while (sel.options.length > 1) sel.remove(1);
    data.forEach(b => {
      const opt = document.createElement('option');
      opt.value = b.name; opt.textContent = b.name;
      opt.dataset.type = b.branch_type || '';        // สาขาหลัก / แฟรนไชส์: the phone's branch sheet groups by it
      sel.appendChild(opt);
    });
    // Restore previous selection if still valid; otherwise show all branches
    if (prev && data.some(b => b.name === prev)) sel.value = prev;
    else sel.value = '';
    branchChipRender();
  } catch(err) {
    console.warn('loadBranches failed:', err);
  }
}

async function loadMonths() {
  const branch = getBranch();
  const url = branch ? `${API}/api/months?branch=${encodeURIComponent(branch)}` : `${API}/api/months`;
  try {
    const result = await fetch(url).then(r => { if (!r.ok) throw new Error('HTTP '+r.status); return r.json(); });
    allMonths = Array.isArray(result) ? result : [];
  } catch(err) {
    console.warn('loadMonths failed:', err);
    allMonths = [];
  }
  if (currentGranularity === 'quarterly') populateQuarterSelects();
  else populateMonthSelects();
  populateCmpDropdowns();
  window.allMonths = allMonths;
  if (typeof gPeriodWrite === 'function') { gPeriodWrite(); gPeriodRender(); }   // PR-PP1: the hidden month fields follow the bar
}

function fmtMonth(ym) {
  const [y,m] = ym.split('-');
  const names = ['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${names[parseInt(m)]} ${y}`;
}

function populateCmpDropdowns() {
  const months = [...allMonths].reverse();
  ['cmpMonth','cmpVs'].forEach((id, idx) => {
    const sel = document.getElementById(id);
    const prev = sel?.value;
    if (!sel) return;
    sel.innerHTML = '';
    months.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m; opt.textContent = fmtMonth(m);
      sel.appendChild(opt);
    });
    // Default: cmpMonth = latest, cmpVs = one before
    if (!prev && months.length > idx) sel.value = months[idx];
    else if (prev && months.includes(prev)) sel.value = prev;
  });
}

// ── Granularity ────────────────────────────────────────────────
let currentGranularity = 'monthly';

// PR-PP1: the top bar's toggle is gone - the ONE period bar (gPeriod*, js/01-core.js) sets currentGranularity
// (week -> weekly, กำหนดเอง -> daily, month / year -> monthly). Kept for callers that still name a granularity.
function setGranularity(g) {
  currentGranularity = g;
  Object.keys(_tabLastKey).forEach(k => delete _tabLastKey[k]);
  loadActiveTab();
}

function populateMonthSelects() {
  const months = [...allMonths].reverse();
  ['selMonthFrom','selMonthTo'].forEach((id, idx) => {
    const sel = document.getElementById(id);
    const prev = sel.value;
    sel.innerHTML = idx === 0
      ? '<option value="">ทั้งหมด</option>'
      : '<option value="">ล่าสุด</option>';
    months.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m; opt.textContent = fmtMonth(m);
      sel.appendChild(opt);
    });
    if (prev && allMonths.includes(prev)) sel.value = prev;
  });
}

function populateQuarterSelects() {
  const sorted = [...allMonths].sort();
  const seen = new Set();
  const quarters = [];
  sorted.forEach(m => {
    const mo = parseInt(m.slice(5), 10);
    const y  = m.slice(0, 4);
    const q  = Math.ceil(mo / 3);
    const key = `${y}-Q${q}`;
    if (!seen.has(key)) {
      seen.add(key);
      const startMo = String((q - 1) * 3 + 1).padStart(2, '0');
      quarters.push({ label: `Q${q} ${y}`, value: `${y}-${startMo}` });
    }
  });
  quarters.reverse(); // newest first
  ['selMonthFrom','selMonthTo'].forEach((id, idx) => {
    const sel = document.getElementById(id);
    const prev = sel.value;
    sel.innerHTML = idx === 0
      ? '<option value="">ทั้งหมด</option>'
      : '<option value="">ล่าสุด</option>';
    quarters.forEach(q => {
      const opt = document.createElement('option');
      opt.value = q.value; opt.textContent = q.label;
      sel.appendChild(opt);
    });
    if (prev) sel.value = prev;
  });
}

// Params of the trend charts: the ช่วงกราฟ window ending at the period bar's period (PR-PP1, gPeriodTrendWindow)
function getTrendParams() {
  const p = new URLSearchParams();
  if (getBranch()) p.set('branch', getBranch());
  const w = gPeriodTrendWindow();
  p.set('granularity', w.g);
  ['date_from', 'date_to', 'month_from', 'month_to'].forEach(k => { if (w[k]) p.set(k, w[k]); });
  return p.toString() ? '?' + p.toString() : '';
}

// Format any period string (YYYY-MM-DD, YYYY-W##, YYYY-MM, YYYY-Q#)
function fmtPeriod(period) {
  if (!period) return '';
  if (/^\d{4}-Q\d$/.test(period))   { const [y,q]=period.split('-'); return `${q} ${y}`; }
  if (/^\d{4}-W\d{2}$/.test(period)) { const [y,w]=period.split('-W'); return `Wk${parseInt(w)} '${y.slice(2)}`; }
  if (/^\d{4}-\d{2}$/.test(period))  return fmtMonth(period);
  if (/^\d{4}-\d{2}-\d{2}$/.test(period)) {
    const d = new Date(period + 'T00:00:00');
    return d.toLocaleDateString('en-GB', { day:'2-digit', month:'short' });
  }
  return period;
}

async function onFilterChange() {
  Object.keys(_tabLastKey).forEach(k => delete _tabLastKey[k]); // bust cache on filter change
  branchChipRender();                                            // the phone's branch chip shows the new branch at once
  await loadMonths();
  loadActiveTab();
}


// ════════════════════════════════════════════════════════════════
// TAB 1 — OVERALL
// ════════════════════════════════════════════════════════════════
async function loadOverall() {
  // loadSummaryKpis must complete first — it creates #kpiMargin which loadMarginTable updates
  const kpiDone = loadSummaryKpis();
  const results = await Promise.allSettled([
    loadAnomalyBanner(),
    kpiDone,
    loadTrendChart(),
    loadCatChart(), loadBreakdownChart(),
    loadTopSkusChart(),
    kpiDone.then(() => loadMarginTable()),
    loadBranchHeatmap(),
    renderExecSummary(),
  ]);
  const names = ['loadAnomalyBanner','loadSummaryKpis','loadTrendChart','loadCatChart','loadBreakdownChart','loadTopSkusChart','loadMarginTable','loadBranchHeatmap','renderExecSummary'];
  results.forEach((r, i) => {
    if (r.status === 'rejected') console.error(names[i] + ' failed:', r.reason);
  });
}

// Latest-month hero: leads with the latest month + MoM (monthly-framed, not a
// cumulative sum). Called twice — first with just trendData (fast core), then
// re-rendered with gpb + disc to add GP / discount tiles + best/worst branch.
// Executive summary — one card tying THIS month's revenue forecast to the target
// and GP health. Surfaces what's only otherwise buried on the trend line.
async function renderExecSummary() {
  const host = document.getElementById('execSummary'); if (!host) return;
  const bq = new URLSearchParams();
  if (getBranch()) bq.set('branch', getBranch());
  if (getGroup())  bq.set('group', getGroup());
  let fc;
  try { fc = await api(`${API}/api/forecast?${bq}`); }
  catch (e) { host.classList.add('hidden'); return; }
  if (!fc || !fc.available) { host.classList.add('hidden'); return; }
  // GP for the same month + total revenue target for that month (parallel)
  const pq = new URLSearchParams(bq); pq.set('month', fc.period);
  const [prof, tg] = await Promise.all([
    api(`${API}/api/profit/summary?${pq}`).catch(() => null),
    api(`${API}/api/targets?month=${fc.period}`).catch(() => []),
  ]);
  const tgtTotal = fc.target_scope ? (fc.target || 0) : (tg || []).reduce((s, r) => s + (r.revenue_target || 0), 0);
  // DV1: the one % of target (/api/forecast, like for like = the management summary's); the old sum is a fallback only
  const projVsTgt = fc.target_scope ? fc.proj_vs_target_pct : (tgtTotal > 0 ? fc.projected / tgtTotal * 100 : null);
  const gp = prof && prof.margin_pct != null ? prof.margin_pct : null;
  const gpColor = gp == null ? 'var(--text-muted)' : gp >= 40 ? 'var(--good-ink)' : gp >= 30 ? '#f59e0b' : 'var(--bad-ink)';
  const tgtColor = projVsTgt == null ? 'var(--text-muted)' : projVsTgt >= 100 ? 'var(--good-ink)' : projVsTgt >= 90 ? '#f59e0b' : 'var(--bad-ink)';
  const tile = (label, value, sub, color) => `<div style="flex:1;min-width:150px">
      <div class="text-[11px]" style="color:var(--text-muted)">${label}</div>
      <div class="text-xl font-bold" style="color:${textInk(color || 'var(--text-primary)')}">${value}</div>
      <div class="text-[11px]" style="color:var(--text-muted)">${sub}</div></div>`;
  // plain-language headline
  const onPace = projVsTgt == null ? '' : projVsTgt >= 100 ? 'เกินเป้า ✓' : projVsTgt >= 90 ? 'ใกล้เป้า ⚠' : 'ต่ำกว่าเป้า ●';
  const gpWord = gp == null ? '' : gp >= 40 ? 'แข็งแรง' : gp >= 30 ? 'พอใช้' : 'ต่ำ';
  const narrative = `เดือน ${fmtMonth(fc.period)}: ทำไปแล้ว ${fc.days_elapsed}/${fc.days_total} วัน · คาดการณ์รายได้ทั้งเดือน <b>${fmt(fc.projected)}</b>` +
    (projVsTgt != null ? ` (<b style="color:${textInk(tgtColor)}">${projVsTgt.toFixed(0)}% ของเป้า</b> ${onPace}${fc.target_scope && fc.target_scope.no_target_branches ? ` · เฉพาะ ${fc.target_scope.branches} สาขาที่มีเป้า` : ''})` :' · <span style="opacity:.7">ยังไม่ได้ตั้งเป้า — ไปที่ Settings → Monthly Targets</span>') +
    (gp != null ? ` · GP <b style="color:${textInk(gpColor)}">${gp.toFixed(1)}%</b> (${gpWord})` : '');
  host.classList.remove('hidden');
  host.innerHTML = `<div class="card p-4" style="background:var(--accent-soft);border:1px solid var(--accent)">
    <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
      <h2 class="section-title mb-0">สรุปผู้บริหาร</h2>
      <span class="text-[11px]" style="color:var(--text-muted)">${getBranch() || 'ทุกสาขา'} · อัปเดตตามข้อมูลล่าสุด</span>
    </div>
    <div class="flex flex-wrap gap-5 mb-2">
      ${tile('คาดการณ์เดือนนี้', fmt(fc.projected), `ช่วง ${fmt(fc.lo)}–${fmt(fc.hi)} · ทำไป ${fc.days_elapsed}/${fc.days_total} วัน`, '#4a70b0')}
      ${tile('เทียบเป้ารายได้', projVsTgt == null ? '—' : projVsTgt.toFixed(0) + '%', tgtTotal > 0 ? `เป้า ${fmt(tgtTotal)}` : 'ยังไม่ตั้งเป้า', tgtColor)}
      ${tile('GP เดือนนี้', gp == null ? '—' : gp.toFixed(1) + '%', 'กำไรขั้นต้น (BoM)', gpColor)}
      ${tile('ทำไปแล้ว', `${fc.days_elapsed}/${fc.days_total} วัน`, `MTD ${fmt(fc.mtd)}`, '#0891b2')}
    </div>
    <p class="text-xs" style="color:var(--text-secondary)">${narrative}</p>
  </div>`;
}

let _heroMtd = null;   // fair MTD-vs-MTD payload for the latest (partial) month (the fallback when /api/period/* fails)
let _heroPeriod = null;   // PR-PP2: /api/period/sales for the top bar's period (the sales tiles, the KPI cards, best / worst branch)
let _heroCost = null;     // PR-PP3: /api/period/cost for the same period (the GP / COGS tiles)
function renderKpiHero(trendData, gpb, disc) {
  const wrap = document.getElementById('kpiHeroWrap');
  const el   = document.getElementById('kpiHero');
  if (!wrap || !el) return;
  const t = (trendData || []).filter(r => (r.revenue || 0) > 0);
  if (t.length < 1) { wrap.classList.add('hidden'); return; }
  const last = t[t.length - 1];
  const prev = t.length >= 2 ? t[t.length - 2] : null;
  let gpSeriesM = [], cogsSeriesM = [];
  // Fair comparison: when the latest month is incomplete, compare month-to-date
  // (1→latest day) vs the SAME day-range of the previous month — not full prev month.
  const mtd = (_heroMtd && _heroMtd.available && _heroMtd.partial) ? _heroMtd : null;
  const mtdSub = mtd ? `MTD 1–${mtd.cutoff_day} · เทียบช่วงเดียวกันเดือนก่อน` : null;
  const revOf = r => r ? (r.revenue_ex_vat ?? r.revenue ?? 0) : 0;
  const phOf  = r => (r && r.customer_count) ? revOf(r) / r.customer_count : 0;
  const mom = (cur, pv) => (pv && pv > 0) ? (cur - pv) / pv * 100 : null;
  const badge = (p, goodUp = true) => p == null ? '<span class="text-slate-300 text-xs">—</span>'
    : `<span class="text-xs font-semibold" style="color:${(p>=0)===goodUp?'var(--good-ink)':'var(--bad-ink)'}">${p>=0?'▲':'▼'} ${Math.abs(p).toFixed(1)}%</span>`;
  const spark = (arr, color) => (typeof mkSparkline === 'function' && arr.length >= 2) ? mkSparkline(arr.slice(-6), color) : '';
  const sparkOf = field => t.map(r => r[field] || 0);

  // Next-month revenue forecast — OLS on the monthly series (≥3 pts). PR-PP2: on COMPLETE months only — the month
  // in progress (the month of the last closed day, not yet at its last day) would pull the line down.
  const _anc = (typeof gPeriodAnchor === 'function') ? gPeriodAnchor() : '';
  const _partialLast = !!(_anc && last.period_month === _anc.slice(0, 7) && _anc < _lastDayOfMonth(_anc.slice(0, 7)));
  const tFull = _partialLast ? t.slice(0, -1) : t;
  let fc = null, nextMo = null;
  if (tFull.length >= 3) {
    const ys = tFull.map(revOf), n = ys.length, xs = ys.map((_, i) => i);
    const mx = xs.reduce((a,b)=>a+b,0)/n, my = ys.reduce((a,b)=>a+b,0)/n;
    let num=0, den=0; for (let i=0;i<n;i++){ num+=(xs[i]-mx)*(ys[i]-my); den+=(xs[i]-mx)**2; }
    const slope = den?num/den:0;
    const ahead = _partialLast ? 1 : 0;           // the month after the one in progress
    fc = Math.max(0, my + slope*(n + ahead - mx));
    const [yy, mm] = (last.period_month||'').split('-').map(Number);
    if (yy && mm) nextMo = mm===12 ? `${yy+1}-01` : `${yy}-${String(mm+1).padStart(2,'0')}`;
  }

  // ── GP (฿) + margin% from per-branch monthly (chain aggregate) ──
  let gpTile;
  const gpByMonth = {};
  if (gpb && gpb.rows) {
    gpb.rows.forEach(r => (r.monthly||[]).forEach(m => {
      const g = gpByMonth[m.month] || (gpByMonth[m.month] = { rev:0, cogs:0 });
      g.rev += m.revenue||0; g.cogs += m.cogs||0;
    }));
    const gm = Object.keys(gpByMonth).sort();
    const gpSeries = gm.map(m => gpByMonth[m].rev - gpByMonth[m].cogs);
    gpSeriesM = gpSeries;
    const lm = gm[gm.length-1], pm = gm[gm.length-2];
    const gpLast = lm ? (gpByMonth[lm].rev - gpByMonth[lm].cogs) : 0;
    const gpPrev = pm ? (gpByMonth[pm].rev - gpByMonth[pm].cogs) : null;
    const marginLast = (lm && gpByMonth[lm].rev) ? gpLast/gpByMonth[lm].rev*100 : null;
    gpTile = tile('กำไรขั้นต้น (GP)', fmt(gpLast), badge(mom(gpLast, gpPrev)), '#059669',
      `Margin ${marginLast!=null?marginLast.toFixed(1)+'%':'—'}`, spark(gpSeries, '#059669'));
  } else {
    gpTile = tile('กำไรขั้นต้น (GP)', '<span class="text-slate-300">…</span>', '', '#059669', 'กำลังโหลด');
  }

  // ── COGS (฿) — Revenue − GP; higher = worse so the delta colour is inverted ──
  let cogsTile;
  if (gpb && gpb.rows && Object.keys(gpByMonth).length) {
    const gm = Object.keys(gpByMonth).sort();
    const cogsSeries = gm.map(m => gpByMonth[m].cogs);
    cogsSeriesM = cogsSeries;
    const lm = gm[gm.length-1], pm = gm[gm.length-2];
    const cogsLast = lm ? gpByMonth[lm].cogs : 0;
    const cogsPrev = pm ? gpByMonth[pm].cogs : null;
    const cogsPct  = (lm && gpByMonth[lm].rev) ? cogsLast/gpByMonth[lm].rev*100 : null;
    const cogsMomPct = (cogsPrev && cogsPrev>0) ? (cogsLast-cogsPrev)/cogsPrev*100 : null;
    const cogsBadge = cogsMomPct==null ? '' :
      `<span class="text-xs font-semibold" style="color:${cogsMomPct<=0?'var(--good-ink)':'var(--bad-ink)'}">${cogsMomPct<=0?'▼':'▲'} ${Math.abs(cogsMomPct).toFixed(1)}%</span>`;
    cogsTile = tile('ต้นทุน (COGS)', fmt(cogsLast), cogsBadge, '#ef4444',
      cogsPct!=null?`${cogsPct.toFixed(1)}% ของรายได้ · BoM`:'BoM COGS', spark(cogsSeries, '#ef4444'));
  } else {
    cogsTile = tile('ต้นทุน (COGS)', '<span class="text-slate-300">…</span>', '', '#ef4444', 'กำลังโหลด');
  }

  // ── Discount rate (latest month + MoM in points; lower is better) ──
  let discTile;
  const dtr = (disc && Array.isArray(disc.trend)) ? disc.trend : null;
  if (dtr && dtr.length) {
    const dl = dtr[dtr.length-1], dp = dtr.length>=2 ? dtr[dtr.length-2] : null;
    const rate = dl?.rate, ratePrev = dp?.rate;
    const pts = (rate!=null && ratePrev!=null) ? (rate-ratePrev) : null;
    const ptsHtml = pts==null ? '<span class="text-slate-300 text-xs">—</span>'
      : `<span class="text-xs font-semibold" style="color:${pts<=0?'var(--good-ink)':'var(--bad-ink)'}">${pts<=0?'▼':'▲'} ${Math.abs(pts).toFixed(1)} pp</span>`;
    discTile = tile('% ส่วนลด', rate!=null?rate.toFixed(1)+'%':'—', ptsHtml, '#f43f5e',
      'ของยอดขาย · ต่ำ = ดี', spark(dtr.map(x=>x.rate||0), '#f43f5e'));
  } else {
    discTile = tile('% ส่วนลด', '<span class="text-slate-300">…</span>', '', '#f43f5e', 'กำลังโหลด');
  }

  // ── MTD overrides: recompute GP / COGS tiles on the same day-range basis ──
  if (mtd && _heroMtd.cogs_comparable) {
    const c = mtd.cur, p = mtd.prev;
    const gpC = c.gross_profit, gpP = p.gross_profit;
    const margC = c.revenue_ex_vat ? gpC / c.revenue_ex_vat * 100 : null;
    gpTile = tile('กำไรขั้นต้น (GP)', fmt(gpC), badge(mom(gpC, gpP)), '#059669',
      `Margin ${margC!=null?margC.toFixed(1)+'%':'—'} · ${mtdSub}`, spark(gpSeriesM, '#059669'));
    const cogsMomPct = (p.cogs>0) ? (c.cogs - p.cogs) / p.cogs * 100 : null;
    const cogsBadge = cogsMomPct==null ? '' :
      `<span class="text-xs font-semibold" style="color:${cogsMomPct<=0?'var(--good-ink)':'var(--bad-ink)'}">${cogsMomPct<=0?'▼':'▲'} ${Math.abs(cogsMomPct).toFixed(1)}%</span>`;
    const cogsPct = c.revenue_ex_vat ? c.cogs / c.revenue_ex_vat * 100 : null;
    cogsTile = tile('ต้นทุน (COGS)', fmt(c.cogs), cogsBadge, '#ef4444',
      `${cogsPct!=null?cogsPct.toFixed(1)+'% ของรายได้ · ':''}BoM · ${mtdSub}`, spark(cogsSeriesM, '#ef4444'));
  }
  // ── MTD overrides: discount rate on the same day-range basis (pp) ──
  if (mtd) {
    const c = mtd.cur, p = mtd.prev;
    const rateC = (c.revenue_ex_vat + c.discount) ? c.discount / (c.revenue_ex_vat + c.discount) * 100 : null;
    const rateP = (p.revenue_ex_vat + p.discount) ? p.discount / (p.revenue_ex_vat + p.discount) * 100 : null;
    const pts = (rateC!=null && rateP!=null) ? (rateC - rateP) : null;
    const ptsHtml = pts==null ? '<span class="text-slate-300 text-xs">—</span>'
      : `<span class="text-xs font-semibold" style="color:${pts<=0?'var(--good-ink)':'var(--bad-ink)'}">${pts<=0?'▼':'▲'} ${Math.abs(pts).toFixed(1)} pp</span>`;
    discTile = tile('% ส่วนลด', rateC!=null?rateC.toFixed(1)+'%':'—', ptsHtml, '#f43f5e',
      `ของยอดขาย · ต่ำ = ดี · ${mtdSub}`, spark((disc&&disc.trend)?disc.trend.map(x=>x.rate||0):[], '#f43f5e'));
  }

  // PR-PP2: the sales tiles follow the top bar's period (to date when it is not finished) with เทียบช่วงก่อน + เทียบปีก่อน
  // (/api/period/sales); the GP / COGS tiles the same period from /api/period/cost (PR-PP3, below).
  const HP = (_heroPeriod && _heroPeriod.cur) ? _heroPeriod : null, HR = HP ? HP.period : null;
  const mEl = document.getElementById('kpiHeroMonth');
  if (mEl) {
    if (HR) { mEl.innerHTML = periodQ(HR); periodChip(HR, true); }   // the range shows once, beside the page title
    else if (mtd) {
      mEl.textContent = `${fmtMonth(mtd.cur_month)} (1–${mtd.cutoff_day}) · เทียบ ${fmtMonth(mtd.prev_month)} (1–${mtd.cutoff_day}) · เทียบช่วงวันเท่ากัน (MTD)`;
    } else {
      mEl.textContent = last.period_month ? fmtMonth(last.period_month) + (prev?` · เทียบ ${fmtMonth(prev.period_month)}`:'') : '';
    }
  }
  const hpCmp = (f, unit, upGood) => HP ? periodCmpHtml(HP.cur[f], HP.prev && HP.prev[f], HP.ly && HP.ly[f], { unit, upGood, range: HR, q: false }) : '';
  if (HP) {
    discTile = tile('% ส่วนลด', HP.cur.discount_pct != null ? HP.cur.discount_pct.toFixed(1) + '%' : '—', '', '#f43f5e',
      'ของยอดขาย · ต่ำ = ดี', spark(dtr ? dtr.map(x => x.rate || 0) : [], '#f43f5e'), hpCmp('discount_pct', 'pp', false));
  }

  // PR-PP3: the GP / COGS tiles follow the same top-bar period as the sales tiles (/api/period/cost: to date through the
  // last closed day, the same days of the period before and of the year before; COGS down = good). A base with no cost
  // source shows "—" with its reason in the "?" of the period.
  const HC = (_heroCost && _heroCost.cur) ? _heroCost : null;
  if (HC) {
    const c = HC.cur, cp = HC.prev || {}, cl = HC.ly || {}, CR = HC.period || HR;
    const cc = (f, unit, upGood) => periodCmpHtml(c[f], HC.prev ? cp[f] : null, HC.ly ? cl[f] : null, { unit, upGood, range: CR, q: false });
    gpTile = tile('กำไรขั้นต้น (GP)', fmt(c.gp), '', '#059669',
      `Margin ${c.gp_pct != null ? c.gp_pct.toFixed(1) + '%' : '—'}`, spark(gpSeriesM, '#059669'), cc('gp', '%', true));
    cogsTile = tile('ต้นทุน (COGS)', fmt(c.cogs), '', '#ef4444',
      `${c.food_cost_pct != null ? c.food_cost_pct.toFixed(1) + '% ของรายได้ · ' : ''}BoM`, spark(cogsSeriesM, '#ef4444'), cc('cogs', '%', false));
  }

  function tile(label, value, deltaHtml, accent, sub, sparkHtml, cmpHtml) {
    return `<div class="card p-4" style="border-top:3px solid ${accent}">
      <div class="flex items-center justify-between gap-2"><p class="text-[11px] text-slate-500 uppercase tracking-wide">${label}</p>${deltaHtml||''}</div>
      <p class="text-2xl font-bold" style="color:${textInk(accent)}">${value}</p>
      ${cmpHtml ? `<div class="pkpi-cmp">${cmpHtml}</div>` : ''}
      <div class="flex items-end justify-between gap-2"><p class="text-[11px] text-slate-400">${sub||''}</p>${sparkHtml||''}</div>
    </div>`;
  }

  // MTD-aware deltas/subs for the sales tiles (fall back to monthly when complete)
  const revBadge  = mtd ? badge(mom(mtd.cur.revenue_ex_vat, mtd.prev.revenue_ex_vat)) : badge(mom(revOf(last), revOf(prev)));
  const revSub    = mtd ? `${mtdSub} (฿${fmtN(Math.round(mtd.prev.revenue_ex_vat))})` : (prev?`เดือนก่อน ${fmt(revOf(prev))}`:'รายได้เดือนนี้');
  const custBadge = mtd ? badge(mom(mtd.cur.customers, mtd.prev.customers)) : badge(mom(last.customer_count, prev?.customer_count));
  const custSub   = mtd ? mtdSub : 'จำนวนลูกค้าเดือนนี้';
  const billBadge = mtd ? badge(mom(mtd.cur.bills, mtd.prev.bills)) : badge(mom(last.tx_count, prev?.tx_count));
  const billSub   = mtd ? mtdSub : 'ใบเสร็จเดือนนี้';
  const phCur     = mtd ? (mtd.cur.customers ? mtd.cur.revenue_ex_vat/mtd.cur.customers : 0) : phOf(last);
  const phPrev    = mtd ? (mtd.prev.customers ? mtd.prev.revenue_ex_vat/mtd.prev.customers : 0) : phOf(prev);
  const phBadge   = badge(mom(phCur, phPrev));
  const phSub     = mtd ? `ต่อลูกค้า (ไม่รวม VAT) · ${mtdSub}` : 'ต่อลูกค้า (ไม่รวม VAT)';
  // Next-month forecast vs the most recent COMPLETE month (the partial latest month would be an unfair base)
  const fcBaseObj = _partialLast ? prev : last;
  const fcBase    = revOf(fcBaseObj);
  const fcBadge   = (fc != null && fcBase > 0) ? badge(mom(fc, fcBase)) : '';
  const fcSub     = (fc != null && nextMo)
    ? `${fmtMonth(nextMo)} · เทียบ ${fcBaseObj ? fmtMonth(fcBaseObj.period_month) : 'เดือนก่อน'} (${fmt(fcBase)}) · แนวโน้มเส้นตรง จากเดือนที่ปิดแล้ว`
    : 'ต้องการ ≥3 เดือนที่ปิดแล้ว';
  el.innerHTML = (HP
    ? tile('รายได้ (ไม่รวม VAT)', fmt(HP.cur.revenue), '', '#4a70b0', 'ยอดขายสุทธิ', spark(sparkOf('revenue'), '#4a70b0'), hpCmp('revenue', '%', true))
    : tile('รายได้ (ไม่รวม VAT)', fmt(mtd ? mtd.cur.revenue_ex_vat : revOf(last)), revBadge, '#4a70b0', revSub, spark(sparkOf('revenue'), '#4a70b0'))) +
    (canSeeCost() ? cogsTile + gpTile : '') +     // PL3: the cost and gross-profit tiles are for the roles that hold a cost or profit module
    (HP
      ? tile('ลูกค้า (Pax)', fmtN(HP.cur.customers), '', '#7a66b8', 'จำนวนลูกค้า', spark(sparkOf('customer_count'), '#7a66b8'), hpCmp('customers', '%', true)) +
        tile('จำนวนบิล', fmtN(HP.cur.bills), '', '#10b981', 'ใบเสร็จ', spark(sparkOf('tx_count'), '#10b981'), hpCmp('bills', '%', true)) +
        tile('ยอด/หัว', HP.cur.rev_per_head != null ? fmtN(Math.round(HP.cur.rev_per_head)) : '—', '', '#06b6d4', 'ต่อลูกค้า (ไม่รวม VAT)', spark(t.map(phOf), '#06b6d4'), hpCmp('rev_per_head', '%', true))
      : tile('ลูกค้า (Pax)', fmtN(mtd ? mtd.cur.customers : (last.customer_count||0)), custBadge, '#7a66b8', custSub, spark(sparkOf('customer_count'), '#7a66b8')) +
        tile('จำนวนบิล', fmtN(mtd ? mtd.cur.bills : (last.tx_count||0)), billBadge, '#10b981', billSub, spark(sparkOf('tx_count'), '#10b981')) +
        tile('ยอด/หัว', fmtN(Math.round(phCur)), phBadge, '#06b6d4', phSub, spark(t.map(phOf), '#06b6d4'))) +
    discTile +
    tile('คาดเดือนถัดไป', fc!=null?fmt(fc):'—', fcBadge, '#f59e0b', fcSub);
  wrap.classList.remove('hidden');

  // ── Best / needs-attention branch — MTD-fair MoM (see renderBranchHilite below) ──
  if (gpb) renderBranchHilite();
}

// Best / needs-attention branch card. Reuses /api/sales/daily month clamping so the
// latest (partial) month is compared MTD-vs-MTD against the SAME day-range of the
// prior month — not against a full prior month (which produced bogus −80%/−90% movers).
async function renderBranchHilite() {
  const hl = document.getElementById('kpiBranchHilite');
  if (!hl) return;
  // PR-PP2: the top bar's period (to date) against the same days of the period before (/api/period/sales by branch)
  let hp = _heroPeriod;
  if (!hp || !Array.isArray(hp.branches) || !hp.branches.length) {
    try { hp = await api(`${API}/api/period/sales?${gPeriodQuery({ by_branch: 1 })}`); } catch (e) { return; }
  }
  const r = hp && hp.period;
  const curLbl = r ? r.label_th : '';
  const cmpLbl = r ? `เทียบ ${r.prev_label_th}` : '';
  const d = { branches: (hp && hp.branches || []).map(b => ({ branch: b.branch, revenue: b.cur.revenue, mom_pct: b.rev_prev_pct,
                                                                 mom_rev: b.prev ? b.prev.revenue : null })) };
  const bl = (d.branches || []).filter(b => (b.revenue || 0) > 0 && b.mom_pct != null);
  if (bl.length < 2) { hl.innerHTML = ''; return; }
  const best  = bl.reduce((a, b) => b.mom_pct > (a?.mom_pct ?? -1e9) ? b : a, null);
  const worst = bl.reduce((a, b) => b.mom_pct < (a?.mom_pct ??  1e9) ? b : a, null);
  const hcard = (icon, title, b, color) => `<div class="card p-4 cursor-pointer hover:shadow-md transition-shadow" style="border:1px solid ${color}" onclick="switchTab('benchmark')">
    <div class="flex items-center justify-between"><p class="text-[11px] text-slate-500 uppercase tracking-wide">${icon} ${title}</p>
      <span class="text-sm font-semibold" style="color:${textInk(color)}">${b.mom_pct>=0?'▲':'▼'} ${Math.abs(b.mom_pct).toFixed(1)}%</span></div>
    <p class="text-lg font-bold text-slate-800">${esc(b.branch)}</p>
    <p class="text-[11px] text-slate-400">${curLbl} ${fmt(b.revenue)} · ${cmpLbl} ${fmt(b.mom_rev || 0)}</p>
  </div>`;
  hl.innerHTML = hcard('','สาขาเด่น (โตสุด เทียบช่วงก่อน)', best, '#059669')
               + hcard('⚠','ต้องดู (ตกสุด เทียบช่วงก่อน)', worst, '#dc2626');
}

async function loadSummaryKpis() {
  const gen = loadGen;            // D4: capture generation to drop stale responses
  const branch = getBranch();
  const month  = getMonth();

  // Show skeleton cards while data loads
  const grid = document.getElementById('kpiGrid');
  grid.innerHTML = Array(5).fill(0).map(() => `
    <div class="skeleton-kpi">
      <div class="skeleton" style="height:10px;width:60%;margin-bottom:14px"></div>
      <div class="skeleton" style="height:28px;width:80%;margin-bottom:12px"></div>
      <div class="skeleton" style="height:10px;width:50%"></div>
    </div>`).join('');

  let d, trendData, mtd = null, hp = null, hc = null;
  try {
    const mtdP = new URLSearchParams();
    if (getBranch()) mtdP.set('branch', getBranch());
    if (getGroup()) mtdP.set('group', getGroup());
    [d, trendData, mtd, hp, hc] = await Promise.all([
      getJSON(`/api/summary${summaryParams()}`, { gen }),
      getJSON(`/api/sales/monthly${getTrendParams()}`, { gen }),
      api(`${API}/api/overall/latest-month-mtd?${mtdP}`).catch(() => null),
      api(`${API}/api/period/sales?${gPeriodQuery({ by_branch: 1 })}`).catch(() => null),   // PR-PP2: the top bar's period
      canSeeCost() ? api(`${API}/api/period/cost?${gPeriodQuery()}`).catch(() => null) : Promise.resolve(null),   // PR-PP3
    ]);
  } catch(err) {
    if (err === STALE) return;   // a newer tab/filter load superseded this one
    grid.innerHTML = `<div style="grid-column:1/-1;padding:16px;color:var(--bad-ink);font-size:13px">⚠ Failed to load KPIs: ${err.message || err}</div>`;
    throw err;
  }
  _heroMtd = mtd;   // fair MTD-vs-MTD comparison when the latest month is incomplete
  _heroPeriod = (loadGen === gen) ? hp : _heroPeriod;
  _heroCost = (loadGen === gen) ? hc : _heroCost;

  renderTrendAnalysisBar(trendData);
  renderKpiHero(trendData);
  // Hero extras (GP, discount, best/worst branch) — load async so they don't
  // block the core KPIs; augment the hero when ready.
  // GP / COGS tiles on the same scope as the revenue tiles beside them: the
  // endpoint takes group=, and a selected branch keeps only its own rows
  const gpP = new URLSearchParams();
  if (getGroup()) gpP.set('group', getGroup());
  Promise.all([
    api(`${API}/api/gp/by-branch-month?${gpP}`).then(g => {
      if (g && g.rows && getBranch()) g = { ...g, rows: g.rows.filter(r => r.branch === getBranch()) };
      return g;
    }).catch(() => null),
    api(`${API}/api/analysis/discounts`).catch(() => null),
  ]).then(([gpb, disc]) => { if (loadGen === gen) renderKpiHero(trendData, gpb, disc); });

  // Last 6 months for sparklines
  const recent = trendData.slice(-6);
  const revSpark = recent.map(r => r.revenue || 0);
  const txSpark  = recent.map(r => r.tx_count  || 0);

  // Prior month delta — only when a specific month is selected
  let prevMonth = null;
  if (month) {
    const idx = trendData.findIndex(r => r.period_month === month);
    if (idx > 0) prevMonth = trendData[idx - 1];
  }

  function pctDelta(cur, prev) {
    if (prev == null || cur == null || prev === 0) return null;
    return ((cur - prev) / Math.abs(prev)) * 100;
  }

  // Use ex-VAT revenue throughout — แสดงรายได้ไม่รวม VAT
  // PR-PP2: the cards read the top bar's period from /api/period/sales (to date through the last closed day, so the day
  // still trading is never in them) and compare it with the same days of the period before - never a part month
  // against a whole one. /api/summary stays the fallback.
  const HP = (hp && hp.cur) ? hp : null, HR = HP ? HP.period : null;
  if (HP) d = Object.assign({}, d, { revenue_ex_vat: HP.cur.revenue, revenue: HP.cur.revenue_inc_vat, tx_count: HP.cur.bills, customer_count: HP.cur.customers });
  const revShown = d.revenue_ex_vat ?? d.revenue;
  const prevRevShown = HP ? (HP.prev && HP.prev.revenue) : prevMonth ? (prevMonth.revenue_ex_vat ?? prevMonth.revenue) : null;
  const perHead  = d.customer_count ? revShown / d.customer_count : null;
  const revDelta = HP ? periodPct(revShown, prevRevShown) : prevRevShown ? pctDelta(revShown, prevRevShown) : null;
  const txDelta  = HP ? periodPct(d.tx_count, HP.prev && HP.prev.bills) : prevMonth ? pctDelta(d.tx_count,  prevMonth.tx_count)  : null;
  const gCmp = (f, unit) => HP ? `<div class="pkpi-cmp">${periodCmpHtml(HP.cur[f], HP.prev && HP.prev[f], HP.ly && HP.ly[f], { unit, range: HR, q: false })}</div>` : '';

  const revClass = revDelta == null ? 'kpi-neu' : revDelta >= 0 ? 'kpi-up' : 'kpi-down';
  const txClass  = txDelta  == null ? 'kpi-neu' : txDelta  >= 0 ? 'kpi-up' : 'kpi-down';
  const _useDates = currentGranularity === 'daily' || currentGranularity === 'weekly';
  let _periodScope;
  if (HR) _periodScope = '';                       // the range is the chip beside the page title
  else if (_useDates) {
    const _df = document.getElementById('selDateFrom')?.value;
    const _dt = document.getElementById('selDateTo')?.value;
    _periodScope = (_df && _dt)
      ? `รวม ${fmtPeriod(_df)} → ${fmtPeriod(_dt)}`
      : (_df || _dt ? fmtPeriod(_dt || _df) : 'ตลอดช่วง');
  } else {
    const _mf = getMonthFrom(), _mt = getMonthTo();
    _periodScope = (_mf && _mt && _mf !== _mt)
      ? `รวม ${fmtMonth(_mf)} → ${fmtMonth(_mt)}`
      : (_mf || _mt ? fmtMonth(_mt || _mf) : 'ตลอดช่วง');
  }

  document.getElementById('kpiGrid').innerHTML = `
    <div class="card kpi-card ${revClass} p-4">
      <div class="kpi-card-top"><p class="kpi-label">รายได้รวม <span class="text-[10px] font-normal text-slate-400">(ไม่รวม VAT)</span></p>${trendBadge(revDelta)}</div>
      <p class="kpi-value" style="color:var(--accent)">${fmt(revShown)}</p>${gCmp('revenue', '%')}
      <div class="kpi-card-bottom">
        <p class="kpi-sub">${_periodScope ? _periodScope + ' · ' : ''}รวม VAT ${fmt(d.revenue)}</p>
        ${mkSparkline(revSpark, '#4a70b0')}
      </div>
    </div>
    <div class="card kpi-card ${txClass} p-4">
      <div class="kpi-card-top"><p class="kpi-label">จำนวนบิล</p>${trendBadge(txDelta)}</div>
      <p class="kpi-value" style="color:var(--good-ink)">${fmtN(d.tx_count)}</p>${gCmp('bills', '%')}
      <div class="kpi-card-bottom">
        <p class="kpi-sub">${fmtN(d.customer_count)} ลูกค้า</p>
        ${mkSparkline(txSpark, '#10b981')}
      </div>
    </div>
    <div class="card kpi-card kpi-neu p-4">
      <div class="kpi-card-top"><p class="kpi-label">ยอดบิลเฉลี่ย <span class="text-[10px] font-normal text-slate-400">(ไม่รวม VAT)</span></p></div>
      <p class="kpi-value" style="color:var(--cyan-ink)">${fmtN(d.tx_count ? revShown / d.tx_count : 0)}</p>${gCmp('avg_ticket', '%')}
      <div class="kpi-card-bottom"><p class="kpi-sub">ต่อใบเสร็จ</p></div>
    </div>
    <div class="card kpi-card kpi-neu p-4">
      <div class="kpi-card-top"><p class="kpi-label">เฉลี่ย/ลูกค้า <span class="text-[10px] font-normal text-slate-400">(ไม่รวม VAT)</span></p></div>
      <p class="kpi-value" style="color:var(--accent)">${perHead ? fmtN(Math.round(perHead)) : '—'}</p>${gCmp('rev_per_head', '%')}
      <div class="kpi-card-bottom"><p class="kpi-sub">ต่อหัว</p></div>
    </div>
    <div class="card kpi-card kpi-neu p-4" id="marginCard">
      <div class="kpi-card-top"><p class="kpi-label">% กำไรขั้นต้น</p></div>
      <p id="kpiMargin" class="kpi-value" style="color:var(--text-muted)">—</p>
      <div class="kpi-card-bottom"><p id="kpiMarginNote" class="kpi-sub">อัพโหลดต้นทุนเพื่อดูค่านี้</p></div>
    </div>
  `;

  loadGroupBreakdown();
}

async function loadGroupBreakdown() {
  const el = document.getElementById('groupBreakdown');
  if (!el) return;
  try {
    const [branches, summaryMaster, summaryFranchise] = await Promise.all([
      api(`${API}/api/branches?include_inactive=true`),
      api(`${API}/api/summary?${new URLSearchParams({
        group: 'master',
        ...(getMonthFrom() ? {month_from: getMonthFrom()} : {}),
        ...(getMonthTo()   ? {month_to:   getMonthTo()}   : {}),
      })}`).catch(() => null),
      api(`${API}/api/summary?${new URLSearchParams({
        group: 'franchise',
        ...(getMonthFrom() ? {month_from: getMonthFrom()} : {}),
        ...(getMonthTo()   ? {month_to:   getMonthTo()}   : {}),
      })}`).catch(() => null),
    ]);

    const masters    = (branches || []).filter(b => (b.branch_type || 'master') === 'master');
    const franchises = (branches || []).filter(b => (b.branch_type || 'master') === 'franchise');

    if (!masters.length && !franchises.length) { el.classList.add('hidden'); return; }
    el.classList.remove('hidden');

    const sM = summaryMaster    || {revenue: 0, revenue_ex_vat: 0, tx_count: 0, customer_count: 0};
    const sF = summaryFranchise || {revenue: 0, revenue_ex_vat: 0, tx_count: 0, customer_count: 0};
    const totalEx = (sM.revenue_ex_vat || 0) + (sF.revenue_ex_vat || 0);
    const pctM = totalEx ? (sM.revenue_ex_vat || 0) / totalEx * 100 : 0;
    const pctF = totalEx ? (sF.revenue_ex_vat || 0) / totalEx * 100 : 0;

    const card = (title, color, count, s, pct) => `
      <div class="rounded-xl p-4" style="background:${color}10;border:1px solid ${color}33">
        <div class="flex items-center justify-between mb-2">
          <p class="font-semibold text-sm" style="color:${textInk(color)}">${title}</p>
          <span class="text-xs font-medium px-2 py-0.5 rounded-full" style="background:${color}22;color:${textInk(color)}">${count} สาขา</span>
        </div>
        <p class="text-2xl font-bold" style="color:${textInk(color)}">${fmt(s.revenue_ex_vat || 0)}</p>
        <p class="text-[11px] text-slate-500 mb-2">${pct.toFixed(1)}% ของยอดรวม · รวม VAT ${fmt(s.revenue || 0)}</p>
        <div class="grid grid-cols-3 gap-2 text-[11px]">
          <div><span class="text-slate-400">บิล</span><br><b>${fmtN(s.tx_count || 0)}</b></div>
          <div><span class="text-slate-400">ลูกค้า</span><br><b>${fmtN(s.customer_count || 0)}</b></div>
          <div><span class="text-slate-400">เฉลี่ย/บิล</span><br><b>${fmtN(s.tx_count ? (s.revenue_ex_vat || s.revenue) / s.tx_count : 0)}</b></div>
        </div>
      </div>`;
    document.getElementById('groupBreakdownBody').innerHTML =
      card('Master',    '#4a70b0', masters.length,    sM, pctM) +
      card('Franchise', '#f59e0b', franchises.length, sF, pctF);

    // Branch lists (no chart, just text per user request)
    const listMaster = masters.map(b => b.name).sort().join(' · ');
    const listFranchise = franchises.length ? franchises.map(b => b.name).sort().join(' · ') : '<i class="text-slate-400">— ยังไม่มีสาขาแฟรนไชส์ —</i>';
    document.getElementById('groupBranchesList').innerHTML = `
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
        <div>
          <p class="font-semibold text-indigo-600 mb-1">รายชื่อ Master (${masters.length})</p>
          <p class="text-slate-600 leading-relaxed">${listMaster || '—'}</p>
        </div>
        <div>
          <p class="font-semibold text-amber-600 mb-1">รายชื่อ Franchise (${franchises.length})</p>
          <p class="text-slate-600 leading-relaxed">${listFranchise}</p>
        </div>
      </div>`;
  } catch (e) {
    console.error('loadGroupBreakdown failed:', e);
    el.classList.add('hidden');
  }
}

function renderTrendAnalysisBar(trendData) {
  const el = document.getElementById('trendAnalysisBar');
  if (!el || !trendData || !trendData.length) return;

  const data = trendData;

  const total   = data.reduce((s, r) => s + (r.revenue || 0), 0);
  const avg     = total / data.length;
  const best    = data.reduce((mx, r) => r.revenue > (mx?.revenue || 0) ? r : mx, null);
  const worst   = data.reduce((mn, r) => r.revenue < (mn?.revenue ?? Infinity) ? r : mn, null);
  const first   = data[0];
  const last    = data[data.length - 1];
  const growth  = first && last && first.revenue > 0 ? ((last.revenue - first.revenue) / first.revenue * 100) : null;

  const stat = (label, value, sub = '', color = '#64748b') =>
    `<div>
      <p class="text-xs text-slate-400 mb-0.5">${label}</p>
      <p class="text-sm font-bold" style="color:${textInk(color)}">${value}</p>
      ${sub ? `<p class="text-xs text-slate-400">${sub}</p>` : ''}
    </div>`;

  const periodLbl = {daily:'รายวัน', weekly:'รายสัปดาห์', monthly:'รายเดือน', quarterly:'รายไตรมาส'}[trendGranularity()] || 'ช่วง';
  el.innerHTML = [
    stat('รายได้รวม',    fmt(total), data.length+' '+periodLbl.toLowerCase()+'s', '#4a70b0'),
    stat(periodLbl+' Avg',   fmt(avg), '', 'var(--text-primary)'),
    best  ? stat('สูงสุด',    fmt(best.revenue),  fmtPeriod(best.period_month),  '#10b981') : '',
    worst ? stat('ต่ำสุด', fmt(worst.revenue), fmtPeriod(worst.period_month), '#ef4444') : '',
    growth != null ? stat('เติบโต', (growth>0?'+':'')+growth.toFixed(1)+'%', fmtPeriod(first.period_month)+' → '+fmtPeriod(last.period_month), growth>=0?'#10b981':'#ef4444') : '',
  ].join('<hr class="border-slate-100 my-0.5">');
}

// Render Year-over-Year comparison: each year = its own line on Jan-Dec x-axis
function renderYearlyOverlay(rows) {
  const MONTH_LABELS = ['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
  // Group rows by year
  const byYear = {};
  rows.forEach(r => {
    const y = r.year || (r.period_month || '').slice(0, 4);
    const mn = parseInt(r.month_num || (r.period_month || '').slice(5, 7), 10);
    if (!y || !mn) return;
    if (!byYear[y]) byYear[y] = Array(12).fill(null);
    byYear[y][mn - 1] = scaleRevenue(r.revenue, r.tx_count, r.customer_count);
  });
  const years = Object.keys(byYear).sort();
  // Colour palette — newer years are bolder
  const YEAR_PALETTE = ['#94a3b8', '#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#4a70b0', '#7a66b8'];
  const datasets = years.map((y, idx) => {
    const isLatest = idx === years.length - 1;
    const color = isLatest ? '#4a70b0' : (YEAR_PALETTE[idx % YEAR_PALETTE.length]);
    return {
      label: y,
      data: byYear[y],
      borderColor: color,
      backgroundColor: isLatest ? 'rgba(31,58,107,0.10)' : 'transparent',
      borderWidth: isLatest ? 2.8 : 2,
      borderDash: isLatest ? [] : [5, 4],
      pointRadius: isLatest ? 4 : 3,
      pointBackgroundColor: color,
      tension: 0.3,
      fill: isLatest,
      spanGaps: true,
    };
  });

  // Render KPI strip with YoY summary
  const el = document.getElementById('trendAnalysisBar');
  if (el && years.length >= 2) {
    const latest = byYear[years[years.length - 1]];
    const prior  = byYear[years[years.length - 2]];
    const totLatest = latest.reduce((s,v)=>s+(v||0),0);
    const totPrior  = prior.reduce((s,v)=>s+(v||0),0);
    const yoyPct = totPrior > 0 ? ((totLatest - totPrior) / totPrior * 100) : null;
    const arrow = yoyPct == null ? '' : (yoyPct >= 0 ? '▲' : '▼');
    const yoyColor = yoyPct == null ? '#64748b' : (yoyPct >= 0 ? '#10b981' : '#ef4444');
    const stat = (label, value, sub = '', color = '#64748b') =>
      `<div><p class="text-xs text-slate-400 mb-0.5">${label}</p>
        <p class="text-sm font-bold" style="color:${textInk(color)}">${value}</p>
        ${sub ? `<p class="text-xs text-slate-400">${sub}</p>` : ''}</div>`;
    el.innerHTML = [
      stat('ปีปัจจุบัน', years[years.length - 1], fmt(totLatest), '#4a70b0'),
      stat('ปีก่อน', years[years.length - 2], fmt(totPrior), '#94a3b8'),
      yoyPct != null ? stat('YoY', `${arrow} ${Math.abs(yoyPct).toFixed(1)}%`, '', yoyColor) : '',
      stat('จำนวนปี', String(years.length), `${years[0]} – ${years[years.length - 1]}`, 'var(--text-primary)'),
    ].filter(Boolean).join('<hr class="border-slate-100 my-0.5">');
  }

  mkChart('chartTrend', {
    type: 'line',
    data: { labels: MONTH_LABELS, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'bottom', labels: { color: chartTheme().text, font: { size: 11 }, boxWidth: 14, usePointStyle: true } },
        datalabels: { display: false },
        tooltip: {
          mode: 'index', intersect: false,
          callbacks: {
            title: ctxs => `${MONTH_LABELS[ctxs[0]?.dataIndex] || ''}`,
            label: ctx => ctx.raw == null ? null : ` ${ctx.dataset.label}: ${fmtN(ctx.raw)}`,
            afterBody: ctxs => {
              const idx = ctxs[0]?.dataIndex;
              if (idx == null || years.length < 2) return '';
              const latest = byYear[years[years.length - 1]][idx];
              const prior  = byYear[years[years.length - 2]][idx];
              if (latest != null && prior != null && prior > 0) {
                const pct = ((latest - prior) / prior * 100);
                const arr = pct >= 0 ? '▲' : '▼';
                return [`YoY: ${arr} ${Math.abs(pct).toFixed(1)}%`];
              }
              return '';
            }
          }
        },
      },
      scales: {
        x: { grid: { color: chartTheme().grid }, ticks: { color: chartTheme().text, font: { size: 11 } } },
        y: { grid: { color: chartTheme().grid }, ticks: { color: chartTheme().text, font: { size: 10 }, callback: v => fmt(v) }, beginAtZero: true },
      },
    },
  });
}

async function loadTrendChart() {
  const data = await api(`${API}/api/sales/monthly${getTrendParams()}`);

  // ── Yearly mode: pivot into per-year line series on Jan-Dec axis ────────────
  if (trendGranularity() === 'yearly') return renderYearlyOverlay(data);

  let labels = data.map(d => fmtPeriod(d.period_month));
  const revenues = data.map(d => scaleRevenue(d.revenue, d.tx_count, d.customer_count));

  // Fetch YoY + Targets + Forecast in parallel (monthly granularity only)
  let yoyMap = {}, targetMap = {}, fc = null;
  if (trendGranularity() === 'monthly') {
    const params = new URLSearchParams();
    if (getBranch())    params.set('branch', getBranch());
    if (getGroup())     params.set('group',  getGroup());
    if (getMonthFrom()) params.set('month_from', getMonthFrom());
    if (getMonthTo())   params.set('month_to',   getMonthTo());
    const [yoy, targets, forecast] = await Promise.all([
      api(`${API}/api/analysis/yoy?${params}`).catch(()=>[]),
      Promise.all((allMonths.length ? data : []).map(d =>
        api(`${API}/api/targets?month=${d.period_month}`).catch(()=>[])
          .then(rows => ({ period: d.period_month, total: rows.reduce((s,r)=>s+(r.revenue_target||0),0) }))
      )),
      api(`${API}/api/forecast?${getBranch()?'branch='+encodeURIComponent(getBranch()):''}${getGroup()?'&group='+getGroup():''}`).catch(()=>({available:false})),
    ]);
    yoy.forEach(r => { yoyMap[r.period_month] = r; });
    targets.forEach(t => { if (t.total > 0) targetMap[t.period] = t.total; });
    if (forecast && forecast.available) fc = forecast;
  }

  // Build optional target dataset
  const datasets = [{
    label: 'รายได้',
    data: revenues,
    borderColor: SEMANTIC.revenue,
    backgroundColor: 'rgba(31,58,107,0.08)',
    borderWidth: 2.5,
    pointRadius: 4,
    pointBackgroundColor: SEMANTIC.revenue,
    fill: true,
    tension: 0.35,
  }];
  const unitIsTotal = getUnitMode() === 'total';
  if (unitIsTotal && Object.keys(targetMap).length) {
    datasets.push({
      label: 'เป้าหมาย',
      data: data.map(d => targetMap[d.period_month] || null),
      borderColor: SEMANTIC.discount,
      backgroundColor: 'transparent',
      borderWidth: 2,
      borderDash: [6, 4],
      pointRadius: 0,
      tension: 0,
      fill: false,
    });
  }
  // Forecast tail — OLS on actual monthly revenue, projected to END OF YEAR
  // (Dec of the latest data year). Confidence band widens with the horizon.
  if (unitIsTotal && trendGranularity() === 'monthly' && data.length >= 3) {
    const lastMonth = data[data.length - 1]?.period_month || '';
    const [ly, lm] = lastMonth.split('-').map(Number);
    const horizon = (ly && lm && lm < 12) ? (12 - lm) : 0;
    if (horizon > 0) {
      const ys = revenues, n = ys.length, xs = ys.map((_, i) => i);
      const mx = xs.reduce((a,b)=>a+b,0)/n, my = ys.reduce((a,b)=>a+b,0)/n;
      let num=0, den=0; for (let i=0;i<n;i++){ num+=(xs[i]-mx)*(ys[i]-my); den+=(xs[i]-mx)**2; }
      const slope = den?num/den:0, inter = my - slope*mx;
      const sd = Math.sqrt(ys.reduce((a,y,i)=>a+(y-(inter+slope*i))**2,0)/n);
      const fcArr = ys.map(()=>null), hiArr = ys.map(()=>null), loArr = ys.map(()=>null);
      // anchor at last actual so the dashed line connects
      fcArr[n-1] = ys[n-1]; hiArr[n-1] = ys[n-1]; loArr[n-1] = ys[n-1];
      for (let h = 1; h <= horizon; h++) {
        labels.push(fmtPeriod(`${ly}-${String(lm + h).padStart(2, '0')}`));
        const pred = Math.max(0, inter + slope * (n - 1 + h));
        const cb = 1.28 * sd * Math.sqrt(h);   // ~80% band, widening with horizon
        fcArr.push(pred);
        hiArr.push(Math.max(0, pred + cb));
        loArr.push(Math.max(0, pred - cb));
      }
      datasets.push({
        label: 'พยากรณ์', data: fcArr,
        borderColor: SEMANTIC.margin, backgroundColor: 'rgba(20,184,166,0.15)',
        borderWidth: 2, borderDash: [5,4],
        pointRadius: ctx => ctx.dataIndex >= n ? 4 : 0, pointStyle: 'rectRot',
        tension: 0.2, fill: false,
      });
      datasets.push({
        label: 'Forecast band', data: hiArr,
        borderColor: 'rgba(20,184,166,0.30)', borderWidth: 1, borderDash: [2,2], pointRadius: 0,
        fill: '+1', backgroundColor: 'rgba(20,184,166,0.10)', tension: 0.2,
      });
      datasets.push({
        label: 'Forecast lo', data: loArr,
        borderColor: 'rgba(20,184,166,0.30)', borderWidth: 1, borderDash: [2,2], pointRadius: 0,
        fill: false, tension: 0.2,
      });
    }
  }

  // YoY overlay — prior-year revenue at same period (only if data exists)
  if (unitIsTotal && trendGranularity() === 'monthly' && Object.keys(yoyMap).length) {
    const yoyArr = data.map(d => {
      const ly = yoyMap[d.period_month];
      return (ly && ly.ly_revenue) ? scaleRevenue(ly.ly_revenue, d.tx_count, d.customer_count) : null;
    });
    if (yoyArr.some(v => v != null && v > 0)) {
      datasets.push({
        label: '↻ ปีก่อน',
        data: yoyArr,
        borderColor: '#94a3b8',
        backgroundColor: 'transparent',
        borderWidth: 2,
        borderDash: [4, 4],
        pointRadius: 3,
        pointBackgroundColor: '#94a3b8',
        tension: 0.25,
        fill: false,
      });
    }
  }

  mkChart('chartTrend', {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: trendGranularity()==='monthly' || Object.keys(targetMap).length>0, position:'bottom', labels:{color:chartTheme().text,font:{size:10},boxWidth:14,filter:(li)=>!/Forecast (band|lo)/.test(li.text)} },
        datalabels: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => {
              if (ctx.dataset.label && ctx.dataset.label.includes('Forecast band')) return null;
              if (ctx.dataset.label && ctx.dataset.label.includes('Forecast lo'))   return null;
              const uSuffix = getUnitMode() !== 'total' ? ' ' + unitLabel() : '';
              return ` ${ctx.dataset.label}: ${fmtN(ctx.raw)}${uSuffix}`;
            },
            afterBody: (ctxs) => {
              const ctx = ctxs[0];
              if (!ctx) return '';
              const period = data[ctx.dataIndex]?.period_month;
              const yoy = yoyMap[period];
              const lines = [];
              if (yoy && yoy.ly_revenue) {
                const pct = yoy.yoy_pct;
                const arrow = pct >= 0 ? '▲' : '▼';
                lines.push(`YoY: ${fmtN(yoy.ly_revenue)} (${arrow}${Math.abs(pct).toFixed(1)}%)`);
                // The raw YoY compares whatever traded in each month — with the
                // chain growing 7→27 branches (and partial first/current months)
                // that reads as growth it isn't. Lead with the like-for-like
                // number whenever the raw one is structurally unfair.
                if (!yoy.comparable) {
                  if (yoy.same_store_pct != null) {
                    const a2 = yoy.same_store_pct >= 0 ? '▲' : '▼';
                    lines.push(`เทียบสาขาเดิม: ${a2}${Math.abs(yoy.same_store_pct).toFixed(1)}% (${yoy.same_store_count} สาขา, ${Math.min(yoy.cur_days, yoy.ly_days)} วันเท่ากัน)`);
                  }
                  lines.push(`⚠ ปีก่อนมี ${yoy.ly_branches} สาขา/${yoy.ly_days} วัน · ปีนี้ ${yoy.cur_branches}/${yoy.cur_days}`);
                }
              }
              const tgt = targetMap[period];
              if (tgt) {
                const att = (data[ctx.dataIndex].revenue/tgt*100).toFixed(1);
                lines.push(`Target: ${fmtN(tgt)} · ${att}% attained`);
              }
              lines.push('click to dive in');
              return lines.join('\n');
            }
          }
        }
      },
      scales: {
        x: { ticks: { font:{size:11} } },
        y: { title: { display: getUnitMode() !== 'total', text: unitLabel(), color: chartTheme().text, font:{size:10} },
             ticks: { font:{size:11}, callback: v => fmt(v) } }
      }
    }
  });
}

async function loadCatChart() {
  const data = await api(`${API}/api/sales/category${qp()}`);
  // MoM per category — latest month vs previous month (respects branch filter)
  let momMap = {};
  try {
    const ms = window.allMonths || [];
    const lastM = ms[ms.length - 1], prevM = ms[ms.length - 2];
    if (lastM && prevM) {
      const bq = getBranch() ? `&branch=${encodeURIComponent(getBranch())}` : '';
      const [cl, cp] = await Promise.all([
        api(`${API}/api/sales/category?month=${lastM}${bq}`).catch(() => []),
        api(`${API}/api/sales/category?month=${prevM}${bq}`).catch(() => []),
      ]);
      const pm = {}; cp.forEach(c => pm[c.category] = c.revenue || 0);
      cl.forEach(c => { const pv = pm[c.category]; momMap[c.category] = (pv > 0) ? ((c.revenue - pv) / pv * 100) : null; });
    }
  } catch (e) {}
  const top = data.slice(0, 9);
  const otherRev = data.slice(9).reduce((s,r)=>s+r.revenue,0);
  const labels = top.map(r => r.category);
  const values = top.map(r => r.revenue);
  if (otherRev > 0) { labels.push('Other'); values.push(Math.round(otherRev)); }
  const total = values.reduce((a,b) => a+b, 0);
  const catColors = labels.map(colorForCategory);

  mkChart('chartCat', {
    type: 'doughnut',
    data: { labels, datasets: [{ data: values, backgroundColor: catColors, borderWidth: 2, borderColor: chartTheme().bg }] },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: '58%',
      plugins: {
        legend: { display: false },
        datalabels: {
          display: ctx => ctx.dataset.data[ctx.dataIndex] / total > 0.05,
          formatter: (v) => (v / total * 100).toFixed(1) + '%',
          color: '#fff',
          font: { size: 10, weight: '700' },
        },
        tooltip: { callbacks: {
          label: ctx => ` ${fmtN(ctx.raw)} · ${(ctx.raw/total*100).toFixed(1)}%`
        }},
      },
    }
  });

  // Category breakdown table
  const catTable = document.getElementById('catMixTable');
  if (catTable) {
    catTable.innerHTML = labels.map((lbl, i) => {
      const mom = momMap[lbl];
      const momHtml = mom == null ? '<span class="text-[10px] text-slate-300 w-12 text-right">—</span>'
        : `<span class="text-[10px] font-semibold w-12 text-right" style="color:${mom>=0?'var(--good-ink)':'var(--bad-ink)'}">${mom>=0?'▲':'▼'}${Math.abs(mom).toFixed(0)}%</span>`;
      return `
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-1.5 min-w-0">
          <span class="w-2 h-2 rounded-full flex-shrink-0" style="background:${catColors[i]}"></span>
          <span class="text-xs text-slate-600 truncate" title="${esc(lbl)}">${esc(lbl)}</span>
        </div>
        <div class="flex items-center gap-2 flex-shrink-0">
          <span class="text-[10px] text-slate-400 w-14 text-right tabular-nums">${fmt(values[i])}</span>
          <span class="text-xs font-semibold text-slate-700 w-10 text-right">${(values[i]/total*100).toFixed(1)}%</span>
          ${momHtml}
        </div>
      </div>`;
    }).join('');
  }
}

async function loadBreakdownChart() {
  // Revenue composition by business type (more useful than the old net/disc/
  // refund split — payment-method fields are empty in this dataset).
  const data = await api(`${API}/api/sales/category${qp()}`);
  if (!data.length) {
    if (chartInstances['chartBreakdown']) { try { chartInstances['chartBreakdown'].destroy(); delete chartInstances['chartBreakdown']; } catch(e){} }
    return;
  }
  const groupOf = c => {
    const u = (c || '').toUpperCase();
    if (u.includes('BUFFET'))                       return 'บุฟเฟ่ต์';
    if (u.includes('DELIVERY'))                     return 'เดลิเวอรี';
    if (/DRINK|ALCOHOL|DRINKING|SOFT|BEVERAGE/.test(u)) return 'เครื่องดื่ม';
    if (u.startsWith('OSHINEI') || u.startsWith('(YAKI'))  return 'อาหาร à la carte';
    return 'อื่นๆ';
  };
  const COLORS = { 'บุฟเฟ่ต์':'#4a70b0', 'อาหาร à la carte':'#10b981', 'เครื่องดื่ม':'#f59e0b', 'เดลิเวอรี':'#06b6d4', 'อื่นๆ':'#94a3b8' };
  const ORDER  = ['บุฟเฟ่ต์','อาหาร à la carte','เครื่องดื่ม','เดลิเวอรี','อื่นๆ'];
  const g = {}; data.forEach(r => { const k = groupOf(r.category); g[k] = (g[k] || 0) + (r.revenue || 0); });
  const labels = ORDER.filter(k => (g[k] || 0) > 0);
  const values = labels.map(k => Math.round(g[k]));
  const total  = values.reduce((a,b)=>a+b,0) || 1;

  mkChart('chartBreakdown', {
    type: 'doughnut',
    data: { labels, datasets: [{ data: values, backgroundColor: labels.map(l => COLORS[l]),
             borderWidth: 2, borderColor: chartTheme().bg }] },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: '58%',
      plugins: {
        legend: { position: 'right', labels: { color:chartTheme().text, font:{size:10}, boxWidth:12, padding:7 } },
        datalabels: { display: ctx => ctx.dataset.data[ctx.dataIndex]/total > 0.05,
                      formatter: v => (v/total*100).toFixed(0)+'%', color:'#fff', font:{size:10,weight:'700'} },
        tooltip: { callbacks: { label: ctx => ` ${fmtN(ctx.raw)} · ${(ctx.raw/total*100).toFixed(1)}%` } }
      }
    }
  });
}

async function loadTopSkusChart() {
  const body = document.getElementById('topSkusBody');
  if (!body) return;
  const d = await api(`${API}/api/sales/top_ordered${qp({limit:60, sort:'cogs'})}`).catch(() => null);
  const items = (d && d.items) ? d.items : [];
  if (!items.length) { body.innerHTML = '<tr><td colspan="7" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>'; return; }
  body.innerHTML = items.map((r, i) => {
    const name = esc((r.description || '—').replace(/^\((?:A|B|T)\)\s*/, ''));
    const mom = r.qty_mom_pct;
    const momHtml = mom == null ? '<span class="text-slate-300">—</span>'
      : `<span style="color:${mom>0?'var(--good-ink)':mom<0?'var(--bad-ink)':'var(--text-muted)'}">${mom>0?'▲':mom<0?'▼':''} ${Math.abs(mom).toFixed(1)}%</span>`;
    return `<tr class="border-t border-slate-100 hover:bg-indigo-50/40">
      <td class="py-1.5 pr-3 text-slate-400">${i+1}</td>
      <td class="py-1.5 pr-3 font-mono text-slate-300 text-[10px]">${esc(r.sku_code)}</td>
      <td class="py-1.5 pr-3 max-w-xs truncate" title="${esc(r.description||'')}">${name}</td>
      <td class="py-1.5 pr-3 text-right tabular-nums">${fmtN(r.qty)}</td>
      <td class="py-1.5 pr-3 text-right font-semibold tabular-nums" style="color:var(--bad-ink)">${fmtN(r.cogs)}</td>
      <td class="py-1.5 pr-3 text-right text-slate-600">${r.cogs_share_pct!=null?r.cogs_share_pct.toFixed(1)+'%':'—'}</td>
      <td class="py-1.5 text-right">${momHtml}</td>
    </tr>`;
  }).join('');
}

function _moverRow(m, positive) {
  const color = positive ? '#10b981' : '#ef4444';
  const arrow = positive ? '▲' : '▼';
  const pct = m.is_new ? 'NEW'
            : m.delta_pct == null ? '—'
            : `${m.delta_pct > 0 ? '+' : ''}${m.delta_pct.toFixed(0)}%`;
  // Dish name, cleaned of the (A)/(B) prefix; category shown small underneath.
  const name = (m.name || m.code || '').replace(/^\((?:A|B|EX|T)\)\s*/i, '').trim();
  const cat  = (m.category || '').replace(/^Oshinei\s+/i, '');
  const deltaTxt = (m.delta > 0 ? '+' : '') + fmtN(m.delta);
  // PR-PP2: เทียบปีก่อน beside the change on the period before (— without a year-ago base)
  const ly = m.delta_ly_pct;
  const lyTxt = `<span class="text-xs" style="color:${ly == null ? 'var(--text-muted)' : ly >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'}" title="เทียบปีก่อน">ปีก่อน ${ly == null ? '—' : (ly > 0 ? '+' : '') + ly.toFixed(0) + '%'}</span>`;
  return `<div class="flex items-center justify-between gap-3 py-0.5">
    <span class="min-w-0">
      <span class="text-sm text-slate-700 block truncate" title="${esc(m.name)}">${esc(name)}</span>
      <span class="text-[10px] text-slate-400">${esc(cat)}</span>
    </span>
    <span class="flex items-center gap-2 flex-shrink-0">
      ${lyTxt}
      <span class="text-xs text-slate-400">${deltaTxt}</span>
      <span class="text-sm font-semibold" style="color:${textInk(color)};min-width:52px;text-align:right">${arrow} ${pct}</span>
    </span>
  </div>`;
}

async function loadMarginTable() {   // now: Category Movers (MoM revenue change)
  // Gross-Margin KPI = WHOLE-STORE GP (unified COGS), same figure as Profit /
  // GP รายสาขา / P&L (~39.5%). Was wrongly using /api/cost/analysis which is the
  // à-la-carte Menu-Engineering margin (~70%) and overstated the Overview KPI.
  api(`${API}/api/profit/summary${qp()}`).then(s => {
    const mp = (s.margin_pct != null) ? s.margin_pct : null;
    const el = document.getElementById('kpiMargin');
    const note = document.getElementById('kpiMarginNote');
    if (mp == null) { el.textContent = '—'; return; }
    el.textContent = fmtP(mp);
    el.style.color = mp >= 50 ? 'var(--good-ink)' : mp >= 25 ? 'var(--amber-ink)' : 'var(--bad-ink)';
    if (note) note.textContent = `ต้นทุน BoM ${fmtP(100-mp)} · ครอบคลุม ${s.cost_coverage_pct||0}%`;
  }).catch(()=>{});

  loadMovers();
}

// Populate the Top Menu Movers month pickers once (blank = auto MoM).
function _moversPopulateMonths() {
  ['moversCurMonth', 'moversVsMonth'].forEach((id, idx) => {
    const sel = document.getElementById(id);
    if (!sel || sel.dataset.loaded) return;
    const months = (Array.isArray(window.allMonths) ? window.allMonths : []).slice().reverse();
    const ph = idx === 0 ? 'ล่าสุด (auto)' : 'ก่อนหน้า (auto)';
    sel.innerHTML = `<option value="">${ph}</option>` +
      months.map(m => `<option value="${m}">${fmtMonth(m)}</option>`).join('');
    sel.dataset.loaded = '1';
  });
}

// Top Menu Movers — MoM by default; pick a current month + a comparison month to
// compare any two months. Empty pickers ⇒ the global range vs its previous period.
async function loadMovers() {
  const card = document.getElementById('moversCard');
  if (!card) return;
  _moversPopulateMonths();
  const curM = document.getElementById('moversCurMonth')?.value || '';
  const vsM  = document.getElementById('moversVsMonth')?.value  || '';
  let url;
  if (curM || vsM) {
    const p = new URLSearchParams();
    if (getBranch()) p.set('branch', getBranch());
    p.set('metric', 'qty'); p.set('limit', '30');
    const cur = curM || getMonthTo() || (window.allMonths ? window.allMonths[window.allMonths.length - 1] : '');
    if (cur) { p.set('month_from', cur); p.set('month_to', cur); }
    if (vsM) p.set('vs_month', vsM);
    url = `${API}/api/sales/dish-movers?${p}`;
  } else {
    url = `${API}/api/sales/dish-movers${qp({ metric: 'qty', limit: 30 })}`;
  }
  let data;
  try { data = await api(url); }
  catch (e) { card.classList.add('hidden'); return; }
  if (!data || (!data.gainers?.length && !data.decliners?.length)) {
    card.classList.add('hidden'); return;
  }
  card.classList.remove('hidden');
  const fmtPer = p => (typeof fmtPeriod === 'function' ? fmtPeriod(p) : p);
  const cp = data.current, pp = data.previous, td = data.to_date;
  // PR-PP2: the month in progress is to date - say which days are set against which ("?" holds the windows)
  document.getElementById('moversPeriod').innerHTML = td
    ? `จำนวนที่ขาย (orders) · ${esc(td.label_th)} เทียบ ${esc(td.prev_label_th)} ${periodQ(Object.assign({ kind: 'range', complete: false }, td))}`
    : esc(`จำนวนที่ขาย (orders) · ${fmtPer(cp.from)}${cp.from !== cp.to ? '–' + fmtPer(cp.to) : ''} vs ${fmtPer(pp.from)}${pp.from !== pp.to ? '–' + fmtPer(pp.to) : ''}`);

  const g = document.getElementById('moversGainers');
  const d = document.getElementById('moversDecliners');
  g.innerHTML = data.gainers.length ? data.gainers.map(m => _moverRow(m, true)).join('')
              : '<p class="text-xs text-slate-400 italic">ไม่มีเมนูที่โตขึ้น</p>';
  d.innerHTML = data.decliners.length ? data.decliners.map(m => _moverRow(m, false)).join('')
              : '<p class="text-xs text-slate-400 italic">ไม่มีเมนูที่ลดลง</p>';
}


// ════════════════════════════════════════════════════════════════
// TAB 2 — MENU ANALYSIS
// ════════════════════════════════════════════════════════════════
async function loadMenuSubtab() {
  loadMenuFinder();   // refresh the search/filter finder for current branch+period
  if (activeSubtab === 'mostOrdered') await loadMostOrdered();
  if (activeSubtab === 'mostCost')    await loadMostRev();
  if (activeSubtab === 'engineering') await loadMenuEngineering();
  if (activeSubtab === 'persona')     await loadPersona();
}

// ── Menu Finder: one searchable/sortable list of all food menus ──────────────
let _menuFinderData = [];
async function loadMenuFinder() {
  try {
    const data = await api(`${API}/api/sales/skus${qp({ limit: 200, sort: 'revenue' })}`);
    _menuFinderData = Array.isArray(data) ? data : [];
  } catch(e){ _menuFinderData = []; }
  // populate category dropdown (preserve current choice)
  const sel = document.getElementById('menuFindCat');
  if (sel) {
    const cur = sel.value;
    const cats = [...new Set(_menuFinderData.map(r => r.category).filter(Boolean))].sort();
    sel.innerHTML = '<option value="">ทุกหมวด</option>' + cats.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
    sel.value = cur;
  }
  renderMenuFinder();
}

function renderMenuFinder() {
  const body = document.getElementById('menuFindBody');
  if (!body) return;
  const q    = (document.getElementById('menuFindSearch')?.value || '').trim().toLowerCase();
  const cat  = document.getElementById('menuFindCat')?.value || '';
  const sort = document.getElementById('menuFindSort')?.value || 'revenue';
  let rows = _menuFinderData.filter(r => {
    if (cat && r.category !== cat) return false;
    if (!q) return true;
    return (`${r.description||''} ${r.sku_code||''}`).toLowerCase().includes(q);
  });
  rows.sort((a,b) => (b[sort]??-Infinity) - (a[sort]??-Infinity));
  document.getElementById('menuFindCount').textContent = `${rows.length} เมนู`;
  body.innerHTML = rows.slice(0, 100).map(r => {
    const priced = _hasMenuPrice(r);                 // buffet (B) has no per-dish price
    const m  = priced ? r.margin_pct : null;
    const mc = m==null ? '#94a3b8' : m>=60?'#10b981':m>=40?'#f59e0b':'#ef4444';
    const mShow = !priced
      ? '<span class="text-[10px]" style="color:var(--amber-ink)" title="บุฟเฟต์ — รวมในแพ็กเกจ ไม่มีราคาขายรายจาน">บุฟเฟต์</span>'
      : (m!=null ? m.toFixed(1)+'%' : '—');
    const name = esc((r.description||r.sku_code||'—').replace(/^\((?:A|B|T)\)\s*/,''));
    return `<tr class="border-b border-slate-50 hover:bg-indigo-50/40">
      <td class="py-1.5 pr-2">${name} <span class="text-[10px] text-slate-300 font-mono">${esc(r.sku_code)}</span></td>
      <td class="py-1.5 pr-2 text-xs text-slate-400">${esc(r.category||'')}</td>
      <td class="py-1.5 pr-2 text-right text-slate-600">${fmtN(r.qty)}</td>
      <td class="py-1.5 pr-2 text-right font-semibold">${fmt(r.revenue)}</td>
      <td class="py-1.5 text-right font-semibold" style="color:${textInk(mc)}">${mShow}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="5" class="py-4 text-center text-slate-400">ไม่พบเมนูที่ตรงกับ “${esc(q)}”</td></tr>`;
}

// ════════════════════════════════════════════════════════════════
// MENU ENGINEERING — BCG matrix + contribution table + velocity bands
// ════════════════════════════════════════════════════════════════
let _meData = null;
let _meSortKey = 'gross_profit';
let _meSortDir = 'desc';

async function loadMenuEngineering() {
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch',     getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  const data = await api(`${API}/api/cost/analysis?${params}`);
  _meData = data;
  renderMenuEngineering();
}

function renderMenuEngineering() {
  if (!_meData) return;
  const items = _meData.items || [];
  const s = _meData.summary || {};
  const costed = items.filter(r => r.unit_cost != null);

  // ── KPI strip (counts per quadrant)
  const byQuad = q => items.filter(r => r.quadrant === q);
  document.getElementById('meStars').textContent  = byQuad('star').length;
  document.getElementById('mePlow').textContent   = byQuad('plowhorse').length;
  document.getElementById('mePuzzle').textContent = byQuad('puzzle').length;
  document.getElementById('meDogs').textContent   = byQuad('dog').length;
  const _bt = document.getElementById('meBranchTag');
  if (_bt) _bt.textContent = getBranch() ? `สาขา: ${getBranch()}` : 'ทุกสาขา';

  // ── Action board (the easy-to-read primary view) ─────────────────────
  renderMenuEngActionBoard(items);

  // ── BCG scatter
  const QUAD = { star:'#10b981', puzzle:'#4a70b0', plowhorse:'#f59e0b', dog:'#ef4444', uncosted:'#94a3b8' };
  const QUAD_LABEL = { star:'Star', puzzle:'Puzzle', plowhorse:'Plowhorse', dog:'Dog', uncosted:'Uncosted' };
  const maxRev = Math.max(1, ...items.map(r=>r.revenue||0));
  // Axis clamping: a few tiny-revenue SKUs have margins of ±thousands-of-% and
  // very-high-qty items stretch X, which squashes every real point into a blob.
  // Clamp the PLOTTED position to a readable window; true values stay in the tooltip.
  const _clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const Y_MIN = -100, Y_MAX = 120;
  // X (popularity) spans 3+ orders of magnitude (median ~300, max ~640k), so a
  // linear axis squashes the cluster. Use a LOG x-axis. Y is clamped to a readable
  // window; extreme-margin tiny-revenue SKUs pin to the edge (true value in tooltip).
  const bcgData = costed.map(r => ({
    x: Math.max(1, r.qty || 0),
    y: _clamp(r.margin_pct == null ? 0 : r.margin_pct, Y_MIN, Y_MAX),
    r: 4 + Math.sqrt((r.revenue || 0)/maxRev) * 18,
    item: r,
    clamped: r.margin_pct != null && (r.margin_pct < Y_MIN || r.margin_pct > Y_MAX),
  }));
  const datasets = ['star','puzzle','plowhorse','dog'].map(q => ({
    label: QUAD_LABEL[q] + ' ('+items.filter(r=>r.quadrant===q).length+')',
    data: bcgData.filter(d => d.item.quadrant === q),
    backgroundColor: QUAD[q] + 'aa',
    borderColor: QUAD[q],
    borderWidth: 1,
  }));
  const avgQty = Math.max(1, s.avg_qty || 0);
  const avgMar = _clamp(s.avg_margin || 0, Y_MIN, Y_MAX);
  mkChart('chartMenuBcg', {
    type: 'bubble',
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top', labels: { color: chartTheme().text, font: { size: 11 } } },
        datalabels: { display: false },
        tooltip: { callbacks: {
          label: ctx => {
            const it = ctx.raw.item;
            return [
              `${it.description}`,
              `Qty: ${fmtN(it.qty||0)}  |  Margin: ${(it.margin_pct||0).toFixed(1)}%`,
              `Revenue: ${fmtN(it.revenue||0)}  |  ${QUAD_LABEL[it.quadrant]}`,
              ...(ctx.raw.clamped ? ['(point clamped to axis edge for readability)'] : []),
            ];
          }
        }},
        annotation: {},
      },
      scales: {
        x: { type: 'logarithmic', title: { display: true, text: 'Quantity sold (log) →', color: chartTheme().text },
             min: 1,
             grid: { color: chartTheme().grid },
             ticks: { color: chartTheme().text, callback: v => {
               const l = Math.log10(v); if (Math.abs(l - Math.round(l)) > 0.01) return ''; return fmtN(v);
             } } },
        y: { title: { display: true, text: 'Margin % →', color: chartTheme().text },
             min: Y_MIN, max: Y_MAX,
             grid: { color: chartTheme().grid }, ticks: { color: chartTheme().text, callback: v => v+'%' } },
      },
    },
  });

  // Draw average-axis lines + quadrant labels via afterDraw plugin (apply once after chart created)
  setTimeout(() => {
    const chart = chartInstances['chartMenuBcg'];
    if (!chart) return;
    const orig = chart.options.plugins?.tooltip?.callbacks?.label;
    chart.options.plugins.afterDraw = true;  // marker so we know
    const ctx = chart.ctx, area = chart.chartArea;
    const xScale = chart.scales.x, yScale = chart.scales.y;
    // Re-render with custom overlay
    chart.update('none');
    // Use after-render to draw guide lines
    requestAnimationFrame(() => {
      if (!chart.chartArea) return;
      const xPos = xScale.getPixelForValue(avgQty);
      const yPos = yScale.getPixelForValue(avgMar);
      ctx.save();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = chartTheme().text + '66';
      ctx.lineWidth = 1;
      // vertical avg-qty line
      ctx.beginPath(); ctx.moveTo(xPos, area.top); ctx.lineTo(xPos, area.bottom); ctx.stroke();
      // horizontal avg-margin line
      ctx.beginPath(); ctx.moveTo(area.left, yPos); ctx.lineTo(area.right, yPos); ctx.stroke();
      ctx.restore();
    });
  }, 100);

  // ── Contribution-margin table
  renderMenuEngTable();

  // ── Velocity bands (split by qty)
  const sortedByQty = [...items].sort((a,b)=>(b.qty||0)-(a.qty||0));
  const fastN = Math.max(1, Math.ceil(items.length * 0.10));
  const slowN = Math.max(1, Math.ceil(items.length * 0.25));
  const fast = sortedByQty.slice(0, fastN);
  const slow = sortedByQty.slice(-slowN);
  const steady = sortedByQty.slice(fastN, sortedByQty.length - slowN);
  const liItem = r => `<li class="flex justify-between gap-2"><span class="truncate">${esc(r.description)}</span><span class="text-slate-400 flex-shrink-0">${fmtN(r.qty||0)}</span></li>`;
  document.getElementById('meFastList').innerHTML   = fast.slice(0,10).map(liItem).join('') || '<li class="italic text-slate-400">—</li>';
  document.getElementById('meSteadyList').innerHTML = steady.slice(0,10).map(liItem).join('') || '<li class="italic text-slate-400">—</li>';
  document.getElementById('meSlowList').innerHTML   = slow.slice(-10).map(liItem).join('') || '<li class="italic text-slate-400">—</li>';
}

// Plain-language 4-group action board — the primary, easy-to-read view.
function renderMenuEngActionBoard(items, hostId = 'meActionBoard', noteId = 'meBoardNote') {
  const host = document.getElementById(hostId);
  if (!host) return;
  const GROUPS = [
    { q:'star',      emoji:'★', title:'ดาวเด่น',   color:'#10b981', bg:'#ecfdf5',
      meaning:'ขายดี + กำไรดี', action:'รักษาคุณภาพ ให้เห็นเด่นในเมนู คุมต้นทุนไว้' },
    { q:'plowhorse', emoji:'', title:'ม้างาน',    color:'#f59e0b', bg:'#fffbeb',
      meaning:'ขายดีแต่กำไรบาง', action:'ขึ้นราคาเล็กน้อย หรือลดต้นทุน/ปรับ portion' },
    { q:'puzzle',    emoji:'', title:'เพชรในตม',  color:'#4a70b0', bg:'#e8eef7',
      meaning:'กำไรดีแต่ขายน้อย', action:'โปรโมท แนะนำที่โต๊ะ/POS จัดเซ็ต ทำให้คนเห็นมากขึ้น' },
    { q:'dog',       emoji:'', title:'ตัวถ่วง',   color:'#ef4444', bg:'#fef2f2',
      meaning:'ขายน้อย + กำไรน้อย', action:'พิจารณาถอดออก หรือปรับสูตร/ราคาใหม่' },
  ];
  const fmtMoney = v => (Number(v)||0).toLocaleString('th-TH', { maximumFractionDigits: 0 });
  host.innerHTML = GROUPS.map(g => {
    const list = items.filter(r => r.quadrant === g.q)
                      .sort((a,b) => (b.revenue||0) - (a.revenue||0));
    const top = list.slice(0, 6);
    const rows = top.map(r => `
      <li class="flex items-baseline justify-between gap-2 py-0.5 border-b border-slate-100 last:border-0">
        <span class="truncate text-slate-700" title="${esc(r.description||'')}">${esc((r.description||r.sku_code||'').slice(0,28))}</span>
        <span class="flex-shrink-0 text-[11px] text-slate-400">${fmtN(r.qty||0)} ขาย · ${r.margin_pct!=null?r.margin_pct.toFixed(0)+'%':'—'}</span>
      </li>`).join('') || '<li class="italic text-slate-400 py-1">— ไม่มีเมนูในกลุ่มนี้ —</li>';
    return `
      <div class="rounded-xl border p-4 on-light" style="background:${g.bg};border-color:${g.color}44">
        <div class="flex items-center justify-between mb-1">
          <span class="font-bold text-sm" style="color:${textInk(g.color)}">${g.emoji} ${g.title}</span>
          <span class="text-lg font-extrabold" style="color:${textInk(g.color)}">${list.length}</span>
        </div>
        <p class="text-[11px] text-slate-500 mb-0.5">${g.meaning}</p>
        <p class="text-[11px] font-medium mb-2" style="color:${textInk(g.color)}">${g.action}</p>
        <ul class="text-xs">${rows}</ul>
        ${list.length > 6 ? `<p class="text-[10px] text-slate-400 mt-1">+ อีก ${list.length-6} เมนู (ดูตารางด้านล่าง)</p>` : ''}
      </div>`;
  }).join('');
  const classified = items.filter(r => ['star','plowhorse','puzzle','dog'].includes(r.quadrant)).length;
  const note = document.getElementById(noteId);
  if (note) note.textContent = `จัดกลุ่มได้ ${classified} จาก ${items.length} เมนู (ที่เหลือยังไม่มีข้อมูลต้นทุน — เติมได้ที่ ตั้งค่า → ต้นทุน)`;
}

function sortMenuEng(key) {
  if (_meSortKey === key) _meSortDir = _meSortDir === 'desc' ? 'asc' : 'desc';
  else { _meSortKey = key; _meSortDir = 'desc'; }
  renderMenuEngTable();
}

function renderMenuEngTable() {
  if (!_meData) return;
  const items = _meData.items || [];
  const QUAD_BADGE = {
    star:      '<span class="pill" style="background:#10b98122;color:var(--good-ink)">ดาวเด่น</span>',
    puzzle:    '<span class="pill" style="background:var(--accent-soft);color:var(--accent)">เพชรในตม</span>',
    plowhorse: '<span class="pill" style="background:#f59e0b22;color:var(--amber-ink)">ม้างาน</span>',
    dog:       '<span class="pill" style="background:#ef444422;color:var(--bad-ink)">ตัวถ่วง</span>',
    uncosted:  '<span class="pill" style="background:#94a3b822;color:#475569">ไม่มีต้นทุน</span>',
  };
  const sorted = [...items].sort((a,b) => {
    const av = a[_meSortKey], bv = b[_meSortKey];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === 'string') return _meSortDir === 'desc' ? bv.localeCompare(av) : av.localeCompare(bv);
    return _meSortDir === 'desc' ? bv - av : av - bv;
  });
  document.getElementById('meTableCount').textContent = `${items.length} SKUs · sorted by ${_meSortKey} ${_meSortDir === 'desc' ? '↓' : '↑'}`;
  document.getElementById('meTableBody').innerHTML = sorted.map(r => {
    const priced = _hasMenuPrice(r);              // buffet (B) has no per-dish price → margin undefined
    const mp = priced ? r.margin_pct : null;
    const mpColor = mp == null ? 'text-slate-400'
                  : mp >= 60 ? 'text-emerald-600 font-medium'
                  : mp >= 30 ? 'text-amber-600'
                  : 'text-red-500 font-medium';
    const mpShow = !priced
      ? '<span class="text-[10px]" style="color:var(--amber-ink)" title="บุฟเฟต์ — รวมในแพ็กเกจ ไม่มีราคาขายรายจาน">บุฟเฟต์</span>'
      : (mp != null ? mp.toFixed(1)+'%' : '—');
    const gpShow = !priced ? '<span class="text-slate-300">—</span>'
      : (r.gross_profit != null ? fmtN(r.gross_profit) : '—');
    const gpCls = (!priced || r.gross_profit == null) ? 'text-slate-400' : (r.gross_profit < 0 ? 'text-red-500' : 'text-emerald-600');
    return `
      <tr class="border-t border-slate-100 hover:bg-slate-50">
        <td class="py-1.5 pr-2"><span class="font-mono text-indigo-600">${r.sku_code}</span> <span class="text-slate-700">${(r.description||'').slice(0,50)}</span></td>
        <td class="py-1.5 pr-2 text-slate-500">${r.category||'—'}</td>
        <td class="py-1.5 pr-2 text-right">${fmtN(r.qty||0)}</td>
        <td class="py-1.5 pr-2 text-right">${fmtN(r.revenue||0)}</td>
        <td class="py-1.5 pr-2 text-right">${r.unit_cost != null ? fmtN(r.unit_cost) : '<span class="text-slate-300">—</span>'}</td>
        <td class="py-1.5 pr-2 text-right ${gpCls}">${gpShow}</td>
        <td class="py-1.5 pr-2 text-right ${mpColor}">${mpShow}</td>
        <td class="py-1.5 text-center">${priced ? (QUAD_BADGE[r.quadrant] || '') : '<span class="pill" style="background:#fef3c7;color:#92400e">บุฟเฟต์</span>'}</td>
      </tr>`;
  }).join('');
}

function exportMenuEngTable() {
  if (!_meData) return;
  const items = _meData.items || [];
  const rows = [['sku_code','description','category','qty','revenue','unit_cost','gross_profit','margin_pct','quadrant'].join(',')];
  items.forEach(r => {
    rows.push([
      r.sku_code, `"${(r.description||'').replace(/"/g,'""')}"`, r.category||'',
      r.qty||0, r.revenue||0, r.unit_cost||'',
      r.gross_profit==null?'':r.gross_profit, r.margin_pct==null?'':r.margin_pct, r.quadrant
    ].join(','));
  });
  downloadCSV(rows.join('\n'), `menu_engineering_${new Date().toISOString().slice(0,10)}.csv`);
}

async function loadMostOrdered() {
  // Two Top-60 views from one endpoint: ranked by sales (qty) and by cost/head (cogs).
  const [dQ, dC] = await Promise.all([
    api(`${API}/api/sales/top_ordered${qp({ limit: 60, sort: 'qty' })}`).catch(() => null),
    api(`${API}/api/sales/top_ordered${qp({ limit: 60, sort: 'cogs' })}`).catch(() => null),
  ]);
  _renderTop60Sales(dQ);
  _renderTop60CostHead(dC);
}

// signed ▲/▼ % with directional colour. goodUp=false → up is bad (used for cost).
function _momSpan(pct, goodUp = true) {
  if (pct == null) return '<span class="text-slate-300">—</span>';
  const c = pct === 0 ? '#94a3b8' : ((pct >= 0) === goodUp ? '#10b981' : '#ef4444');
  return `<span style="color:${textInk(c)};font-weight:600">${pct>0?'▲':pct<0?'▼':''} ${Math.abs(pct).toFixed(1)}%</span>`;
}
function _top60MomLabel(d, prefix) {
  // PR-PP2: the compared windows (this scope's last closed day) when the month is not finished
  if (d && d.mom_basis === 'mtd' && Array.isArray(d.cmp_range) && Array.isArray(d.cmp_prev_range))
    return `${prefix}${periodLabelTh(d.cmp_prev_range[0], d.cmp_prev_range[1])} (วันเท่ากัน)`;
  return (d && d.mom_basis === 'mtd') ? `${prefix}1–${d.mtd_cutoff_day} วันเท่ากัน (MTD)` : `${prefix}เดือนก่อน`;
}

// ABC class badge from a running cumulative-share % (Pareto): A ≤80%, B ≤95%, C tail.
function _abcBadge(cum) {
  const c = cum <= 80 ? 'A' : cum <= 95 ? 'B' : 'C';
  const s = c === 'A' ? 'background:#dcfce7;color:#166534' : c === 'B' ? 'background:#fef9c3;color:#854d0e' : 'background:#f1f5f9;color:#5f6c80';
  return `<span style="${s};font-weight:700;font-size:10px;padding:1px 6px;border-radius:999px">${c}</span>`;
}
function _renderTop60Sales(d) {
  const items = (d && d.items) || [];
  const noteEl = document.getElementById('orderedMomNote');
  // PR-MB: the reading of the columns is behind the "?" beside the heading (the line under it stays empty)
  if (noteEl) noteEl.innerHTML = esc(_top60MomLabel(d, 'MoM เทียบ ')) + ' ' + uiQ('menu:top60-abc', 'ABC และ Pareto', '<p>ABC = Pareto (A ≤80% สะสม · B 80–95% · C หาง) · เทรนด์ = จำนวนขายรายเดือน · ต้นทุนรวม = ต้นทุน BoM รวมของเมนู (ต้นทุน/จาน × จำนวนขาย) · % สะสม COGS = Pareto สัดส่วนต้นทุนสะสม (เมนูแรกๆ ที่ทำให้ถึง 80% = ตัวขับต้นทุนหลัก)</p>');
  const cn = document.getElementById('orderedCoverNote');
  if (cn) cn.textContent = '';
  const body = document.getElementById('orderedBody'), foot = document.getElementById('orderedFoot');
  if (!items.length) { body.innerHTML = '<tr><td colspan="11" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>'; if (foot) foot.innerHTML = ''; return; }
  // prev qty (from qty MoM%) drives the total-MoM% in the footer
  const prevQtyOf = r => (r.qty_mom_pct != null && r.qty_mom_pct != -100) ? (r.qty || 0) / (1 + r.qty_mom_pct / 100) : null;
  const f2 = v => (v == null ? '—' : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  let sumQty = 0, sumShare = 0, sumPrev = 0, sumCogs = 0, cum = 0, cumCogs = 0;
  body.innerHTML = items.map((r, i) => {
    const name = esc((r.description || '—').replace(/^\((?:A|B|T)\)\s*/, ''));
    const mom = r.qty_mom_pct;
    const pq = prevQtyOf(r);
    cum += r.qty_share_pct || 0;
    sumQty += r.qty || 0; sumShare += r.qty_share_pct || 0; if (pq != null) sumPrev += Math.round(pq);
    // PR-PP2: เทียบช่วงก่อน on top, เทียบปีก่อน under it (— without a year-ago base)
    const momCell = (mom == null ? '<span class="text-amber-600">เมนูใหม่</span>' : _momSpan(mom, true))
      + `<div class="text-xs" title="เทียบปีก่อน" aria-label="เทียบปีก่อน">${_momSpan(r.qty_yoy_pct, true)}</div>`;   // narrow: the column header names the line
    const cogs = r.cogs || 0; sumCogs += cogs;
    cumCogs += r.cogs_share_pct || 0;                                // running Pareto of COGS share
    const costCell = cogs > 0 ? fmtN(cogs) : '<span class="text-slate-300">—</span>';  // TOTAL cost (BoM)
    const cumCogsCell = r.cogs_share_pct != null ? cumCogs.toFixed(1) + '%' : '<span class="text-slate-300">—</span>';
    const spk = (typeof mkSparkline === 'function' && Array.isArray(r.spark) && r.spark.length >= 2) ? mkSparkline(r.spark.slice(-8), '#4a70b0', 64, 22) : '';
    return `<tr>
      <td class="num tabular text-slate-400">${i + 1}</td>
      <td class="num">${_abcBadge(cum)}</td>
      <td class="font-mono text-slate-400 text-[10px]">${esc(r.sku_code)}</td>
      <td class="max-w-xs truncate" title="${esc(r.description || '')}">${name}</td>
      <td class="num">${spk}</td>
      <td class="num tabular font-semibold">${fmtN(r.qty)}</td>
      <td class="num tabular text-slate-600">${r.qty_share_pct != null ? r.qty_share_pct.toFixed(1) + '%' : '—'}</td>
      <td class="num tabular text-slate-400">${cum.toFixed(1)}%</td>
      <td class="num tabular font-medium text-orange-700">${costCell}</td>
      <td class="num tabular text-slate-400">${cumCogsCell}</td>
      <td class="num tabular">${momCell}</td>
    </tr>`;
  }).join('');
  if (foot) foot.innerHTML = `<tr>
    <td></td><td></td><td></td><td>รวม Top 60</td><td></td>
    <td class="num tabular">${fmtN(sumQty)}</td>
    <td class="num tabular">${sumShare.toFixed(1)}%</td>
    <td class="num tabular">${cum.toFixed(1)}%</td>
    <td class="num tabular font-medium text-orange-700">${sumCogs > 0 ? fmtN(sumCogs) : '—'}</td>
    <td class="num tabular text-slate-400">${cumCogs.toFixed(1)}%</td>
    <td class="num tabular">${_momSpan(sumPrev > 0 ? (sumQty - sumPrev) / sumPrev * 100 : null, true)}</td>
  </tr>`;
}

function _renderTop60CostHead(d, ids) {
  // ids lets a second placement (e.g. §4 of the Analysis Report) reuse this exact
  // renderer so the numbers match the Menu tab. Defaults to the Menu-tab elements.
  ids = ids || { body:'costHeadBody', foot:'costHeadFoot', momNote:'costHeadMomNote', note:'costHeadNote' };
  const items = (d && d.items) || [];
  const covers = (d && d.total_covers) || 0;
  const cCur = (d && d.cmp_covers_cur) || 0, cPrev = (d && d.cmp_covers_prev) || 0;
  const coversRatio = (cCur > 0 && cPrev > 0) ? cPrev / cCur : null;   // prev ÷ cur
  const noteEl = document.getElementById(ids.momNote);
  // PR-MB: the reading of the columns is behind the "?" beside the heading; the line under it keeps only the no-covers state
  if (noteEl) noteEl.innerHTML = esc(_top60MomLabel(d, 'เทียบ ')) + (covers ? ' ' + uiQ('menu:top60-costhead', 'อ่านตารางต้นทุนต่อหัว', `<p>จำนวนเสิร์ฟ = จำนวนจาน/ที่ขายในช่วงนี้ (จำนวน % = เทียบเดือนก่อน · ขึ้น = ขายดีขึ้น) · ต้นทุน/หัว = ต้นทุน BoM ÷ ลูกค้าทั้งหมด ${fmtN(covers)} หัว (บาท/หัว · ทศนิยม 2) · % ของต้นทุนรวม = สัดส่วน COGS ของเมนู · % สะสม = Pareto ต้นทุน · เปลี่ยนแปลง % รวมผลต้นทุน+จำนวนลูกค้า (ขึ้น = แย่)</p>`) : '');
  const cn = document.getElementById(ids.note);
  if (cn) cn.textContent = covers ? '' : 'ต้นทุน/หัว: ไม่มีข้อมูลจำนวนลูกค้าในช่วงนี้';
  const body = document.getElementById(ids.body), foot = document.getElementById(ids.foot);
  if (!items.length || !covers) { body.innerHTML = '<tr><td colspan="11" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>'; if (foot) foot.innerHTML = ''; return; }
  const f2 = v => (v == null ? '—' : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  let sumCur = 0, sumPrev = 0, sumShare = 0, cum = 0, sumQty = 0, sumQtyPrev = 0;
  body.innerHTML = items.map((r, i) => {
    const name = esc((r.description || '—').replace(/^\((?:A|B|T)\)\s*/, ''));
    const chCur = (r.cogs || 0) / covers;
    const qm = r.qty_mom_pct;   // = cogs MoM (BoM recipe unit cost is constant across periods)
    const chMomFrac = (qm != null && coversRatio != null) ? (1 + qm / 100) * coversRatio - 1 : null;
    const chPrev = (chMomFrac != null && chMomFrac > -1) ? chCur / (1 + chMomFrac) : null;
    cum += r.cogs_share_pct || 0;
    sumCur += chCur; if (chPrev != null) sumPrev += chPrev; sumShare += r.cogs_share_pct || 0;
    sumQty += r.qty || 0;
    const qtyPrev = (qm != null && qm != -100) ? (r.qty || 0) / (1 + qm / 100) : null;
    if (qtyPrev != null) sumQtyPrev += qtyPrev;
    const qtyMomCell = qm == null ? '<span class="text-amber-600">เมนูใหม่</span>' : _momSpan(qm, true);
    return `<tr>
      <td class="num tabular text-slate-400">${i + 1}</td>
      <td class="num">${_abcBadge(cum)}</td>
      <td class="font-mono text-slate-400 text-[10px]">${esc(r.sku_code)}</td>
      <td class="max-w-xs truncate" title="${esc(r.description || '')}">${name}</td>
      <td class="num tabular font-semibold">${fmtN(r.qty)}</td>
      <td class="num tabular">${qtyMomCell}</td>
      <td class="num tabular font-medium text-orange-700">${f2(chCur)}</td>
      <td class="num tabular text-slate-600">${r.cogs_share_pct != null ? r.cogs_share_pct.toFixed(1) + '%' : '—'}</td>
      <td class="num tabular text-slate-400">${cum.toFixed(1)}%</td>
      <td class="num tabular text-slate-400">${f2(chPrev)}</td>
      <td class="num tabular">${_momSpan(chMomFrac != null ? chMomFrac * 100 : null, false)}</td>
    </tr>`;
  }).join('');
  if (foot) {
    const totMom = (sumPrev > 0) ? (sumCur - sumPrev) / sumPrev * 100 : null;
    const totQtyMom = (sumQtyPrev > 0) ? (sumQty - sumQtyPrev) / sumQtyPrev * 100 : null;
    foot.innerHTML = `<tr>
      <td></td><td></td><td></td><td>รวม Top 60</td>
      <td class="num tabular">${fmtN(sumQty)}</td>
      <td class="num tabular">${_momSpan(totQtyMom, true)}</td>
      <td class="num tabular">${f2(sumCur)}</td>
      <td class="num tabular">${sumShare.toFixed(1)}%</td>
      <td class="num tabular">${cum.toFixed(1)}%</td>
      <td class="num tabular">${f2(sumPrev)}</td>
      <td class="num tabular">${_momSpan(totMom, false)}</td>
    </tr>`;
  }
}

let _mostRevData = [];
let _mostRevSort = 'revenue';
async function loadMostRev() {
  // Fetch the full à la carte set once (buffet/drinks already excluded server-side)
  // so coverage and client-side re-sorting work without refetching.
  const data = await api(`${API}/api/sales/skus${qp({limit:200,sort:'revenue'})}`).catch(() => []);
  _mostRevData = Array.isArray(data) ? data : [];
  _renderMostRev();
}
function setMostRevSort(s) {
  _mostRevSort = s;
  document.querySelectorAll('.revsort-btn').forEach(b => b.classList.toggle('active', b.dataset.sort === s));
  _renderMostRev();
}
function _renderMostRev() {
  const body = document.getElementById('revBody');
  if (!body) return;
  const all = _mostRevData;
  if (!all.length) { body.innerHTML = '<tr><td colspan="8" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>'; return; }
  // Coverage: how many menus actually have a cost (the rest can't show margin)
  const costed = all.filter(r => r.unit_cost != null).length;
  const neg = all.filter(r => r.margin_pct != null && r.margin_pct < 0).length;
  const cov = document.getElementById('mostRevCoverage');
  if (cov) cov.innerHTML = `มีข้อมูลต้นทุน <b>${costed}/${all.length}</b> เมนู (${(costed/all.length*100).toFixed(0)}%) · ${all.length - costed} เมนูยังไม่มีต้นทุน → กำไร% แสดง —`
    + (neg ? ` · <span style="color:var(--bad-ink)">⚠ ${neg} เมนูต้นทุน > ราคาขาย (ข้อมูลต้นทุนน่าจะผิด)</span>` : '');
  // Sort a copy by the chosen key (nulls sink to the bottom)
  const sorted = [...all].sort((a, b) => {
    if (_mostRevSort === 'margin') return (b.margin_pct ?? -1e9) - (a.margin_pct ?? -1e9);
    if (_mostRevSort === 'qty')    return (b.qty ?? 0) - (a.qty ?? 0);
    return (b.revenue ?? 0) - (a.revenue ?? 0);
  });
  const rows = sorted.slice(0, 30);
  // Chart: top 15 of the current sort
  const ct = document.getElementById('mostRevChartTitle');
  if (ct) ct.textContent = _mostRevSort === 'margin' ? 'Top 15 กำไร% สูงสุด' : _mostRevSort === 'qty' ? 'Top 15 ขายดีสุด' : 'Top 15 รายได้สูงสุด';
  const top = sorted.slice(0, 15);
  const metric = _mostRevSort === 'margin' ? (r => r.margin_pct) : _mostRevSort === 'qty' ? (r => r.qty) : (r => r.revenue);
  const isPct = _mostRevSort === 'margin';
  mkChart('chartMostRev', {
    type: 'bar',
    data: {
      labels: top.map(r => (r.description || r.sku_code).replace(/^\((?:A|B|T)\)\s*/, '').slice(0, 18)),
      datasets: [{ label:'', data: top.map(metric), backgroundColor:'#4a70b0', borderRadius:4 }]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend:{display:false}, datalabels:{display:false}, tooltip:{callbacks:{
        title: items => top[items[0].dataIndex]?.description || '',
        label: ctx => ' ' + (isPct ? (ctx.raw==null?'—':ctx.raw.toFixed(1)+'%') : fmtN(ctx.raw))
      }} },
      scales: {
        x: { grid:{color:'#f1f5f9'}, ticks:{color:'#94a3b8',font:{size:10},callback:v=>isPct?v+'%':fmt(v)} },
        y: { grid:{display:false}, ticks:{color:'#64748b',font:{size:11}} }
      }
    }
  });
  body.innerHTML = rows.map((r,i) => {
    const mpct = r.margin_pct;
    const isNeg = mpct != null && mpct < 0;
    const mc = mpct==null ? '#94a3b8' : isNeg ? 'var(--bad-ink)' : mpct>=60?'#10b981':mpct>=40?'#f59e0b':'#ef4444';
    const name = esc((r.description||'—').replace(/^\((?:A|B|T)\)\s*/,''));
    const cat = esc((r.category||'').replace(/^Oshinei\s*/i,''));
    return `<tr class="border-t border-slate-100 hover:bg-indigo-50/40${isNeg?' bg-red-50/60':''}">
      <td class="py-1.5 pr-3 text-slate-400">${i+1}</td>
      <td class="py-1.5 pr-3 font-mono text-slate-300 text-[10px]">${esc(r.sku_code)}</td>
      <td class="py-1.5 pr-3 text-xs truncate max-w-[180px]" title="${esc(r.description||'')}">${name}</td>
      <td class="py-1.5 pr-3 text-[10px] text-slate-400">${cat}</td>
      <td class="py-1.5 pr-3 text-right tabular text-slate-600">${fmtN(r.qty)}</td>
      <td class="py-1.5 pr-3 text-right font-semibold">${fmtN(r.revenue)}</td>
      <td class="py-1.5 pr-3 text-right text-slate-500">${r.unit_cost!=null?fmtN(r.unit_cost):'—'}</td>
      <td class="py-1.5 text-right font-semibold" style="color:${textInk(mc)}">${isNeg?'⚠ ':''}${fmtP(mpct)}</td>
    </tr>`;
  }).join('');
}

// SPH Distribution — better viz: stacked bar showing tier % + percentile markers + KPI strip
function renderSphDistribution(sph) {
  // bands ascending by spend: the percentile markers rely on it (P50 = 50 % along the bar)
  const dist = (sph.distribution || []).slice().sort((x, y) => (x.lo || 0) - (y.lo || 0));
  const ov   = sph.overall || {};
  const totalBills = dist.reduce((s, d) => s + (d.count || 0), 0);

  const stripEl  = document.getElementById('sphKpiStrip');
  const stackEl  = document.getElementById('sphStackBar');
  const rulerEl  = document.getElementById('sphRuler');
  const legendEl = document.getElementById('sphTierLegend');
  if (!stripEl || !stackEl || !rulerEl || !legendEl) return;

  if (!totalBills) {
    stripEl.innerHTML = '<p class="text-slate-400 col-span-4 text-center py-6">ไม่มีข้อมูล</p>';
    stackEl.innerHTML = ''; rulerEl.innerHTML = ''; legendEl.innerHTML = '';
    return;
  }

  // KPI strip — median, p75, % of bills in the high-spend band, most common band
  const hiFrom = ov.high_spend_from || 700;
  const kpi = (lbl, val, sub, color) =>
    `<div class="rounded-lg p-2.5 border" style="background:${color}10;border-color:${color}33">
      <p class="text-[10px] text-slate-500 uppercase tracking-wide">${lbl}</p>
      <p class="text-base font-bold" style="color:${textInk(color)}">${val}</p>
      <p class="text-[10px] text-slate-500">${sub}</p>
    </div>`;
  stripEl.innerHTML = [
    kpi('Median (มัธยฐาน)', fmtN(ov.median_sph), `ครึ่งหนึ่งของลูกค้าจ่ายเกินค่านี้`, '#4a70b0'),
    kpi('P75 (ลูกค้าเกรด A)', fmtN(ov.p75), `25% บนสุดจ่ายเกิน ${fmtN(ov.p75)}`, '#10b981'),
    // Stated as the spend it is, not as a tier name: the old label said
    // "เกิน Buffet 590" — a price withdrawn on 1 Sep 2026, and not the cut-off
    // either (the bands that pass it start at ฿700 ≈ ฿789 net on the new menu).
    kpi(`% จ่าย ≥ ฿${hiFrom}/หัว`, `${(ov.pct_above_700 ?? ov.pct_above_590 ?? 0).toFixed(1)}%`,
        `บิลที่ทำกำไรสูง (≈ tier ฿789 เมนูใหม่)`, '#f59e0b'),
    kpi('ช่วงราคาที่นิยม', ov.dominant_tier || '—', `${(ov.dominant_pct || 0).toFixed(1)}% ของบิลทั้งหมด`, '#7a66b8'),
  ].join('');

  // 100% stacked horizontal bar — colored segments per tier
  const tooltipNote = 'คลิกแล้วลากเพื่อ filter ในอนาคต — ตอนนี้เลื่อนเมาส์เพื่อดูรายละเอียด';
  stackEl.innerHTML = dist.map(d => {
    const pct = totalBills ? (d.count / totalBills * 100) : 0;
    if (pct < 0.1) return '';
    const range = d.hi == null ? `≥ ${d.lo}` : `${d.lo}–${Math.round(d.hi)}`;
    const inner = pct > 4
      ? `<div class="text-[11px] font-semibold" style="color:${inkOn(d.color)}">${pct.toFixed(1)}%</div>`
      : '';
    return `<div title="${d.bucket} (${range} บาท/หัว) — ${d.count.toLocaleString()} บิล (${pct.toFixed(1)}%)"
      style="flex:${pct};background:${d.color};display:flex;align-items:center;justify-content:center;cursor:help;transition:filter .15s"
      onmouseover="this.style.filter='brightness(1.15)'" onmouseout="this.style.filter=''">${inner}</div>`;
  }).join('');

  // Percentile ruler. The bar is a share-of-bills bar (segments = bills in each
  // spend band, bands ascending), so the p-th percentile bill sits p % along it:
  // P10 at 10 %, median at 50 %, P90 at 90 %. The old value scale (sph_min ->
  // sph_max) put every marker in the first few % because one outlier bill set the
  // top end, and the labels piled up at the left edge.
  const fs = document.body.classList.contains('ph-on') ? 12 : 11;   // >= 12 px on a phone (แตะง่าย), 11 px on a desktop
  const markers = [
    { p: 'P10',     at: 10, v: ov.p10,        color: '#94a3b8', edge: true },
    { p: 'P25',     at: 25, v: ov.p25,        color: '#64748b' },
    { p: 'มัธยฐาน', at: 50, v: ov.median_sph, color: '#4a70b0' },
    { p: 'P75',     at: 75, v: ov.p75,        color: '#10b981' },
    { p: 'P90',     at: 90, v: ov.p90,        color: '#059669', edge: true },
  ].filter(m => m.v != null);
  rulerEl.style.height = Math.ceil(fs * 2.6 + 12) + 'px';
  rulerEl.innerHTML = markers.map(m => {
    const tip = `${m.p} = ฿${fmtN(m.v)}/หัว · ${m.at}% ของบิลจ่ายต่ำกว่านี้`;
    return `<div class="sph-mk${m.edge ? ' sph-mk-edge' : ''}" data-at="${m.at}" title="${esc(tip)}"
      style="position:absolute;top:0;left:${m.at}%;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;cursor:help">
      <div style="width:2px;height:7px;background:${m.color};border-radius:1px"></div>
      <div class="sph-lbl" style="margin-top:2px;font-size:${fs}px;line-height:1.3;text-align:center;white-space:nowrap;font-weight:600;color:${textInk(m.color)}">${m.p}<br><span style="font-weight:400;color:var(--text-secondary)">฿${fmtN(m.v)}</span></div>
    </div>`;
  }).join('');
  _sphRulerFit(rulerEl);
  if (!rulerEl._sphRo && typeof ResizeObserver === 'function') {
    rulerEl._sphRo = new ResizeObserver(() => _sphRulerFit(rulerEl));
    rulerEl._sphRo.observe(rulerEl);
  }

  // Tier legend grid
  legendEl.innerHTML = dist.map(d => {
    const pct = totalBills ? (d.count / totalBills * 100) : 0;
    const range = d.hi == null ? `≥ ${d.lo}` : `${d.lo}–${Math.round(d.hi)}`;
    return `<div class="flex items-center gap-2 px-2 py-1 rounded border border-slate-200">
      <span style="width:10px;height:10px;border-radius:3px;background:${d.color};flex-shrink:0"></span>
      <div class="flex-1 min-w-0">
        <div class="font-medium text-slate-700 truncate">${d.bucket}</div>
        <div class="text-slate-400 text-[10px]">${range} บาท · ${d.count.toLocaleString()} บิล</div>
      </div>
      <span class="font-semibold" style="color:${textInk(d.color)}">${pct.toFixed(1)}%</span>
    </div>`;
  }).join('');
}

// Collision guard for the SPH percentile ruler: when any two labels would touch
// (a narrow phone), keep only P25 · มัธยฐาน · P75 labelled; P10/P90 keep their
// tick and the tooltip.
function _sphRulerFit(rulerEl) {
  if (!rulerEl || !rulerEl.offsetWidth) return;
  const all = [...rulerEl.querySelectorAll('.sph-mk')];
  all.forEach(m => { m.querySelector('.sph-lbl').style.visibility = ''; });
  const boxes = all.map(m => m.querySelector('.sph-lbl').getBoundingClientRect());
  let clash = false;
  for (let i = 1; i < boxes.length; i++) if (boxes[i].left < boxes[i - 1].right + 4) { clash = true; break; }
  if (!clash) return;
  all.forEach(m => { if (m.classList.contains('sph-mk-edge')) m.querySelector('.sph-lbl').style.visibility = 'hidden'; });
}

// PR-SPHT (owner 2026-10-25): customers by the Grand Menu price tier (from 1 Sep 2026), under the spend bands.
// The answer's sph.tiers = {from, to, clipped, data_to, lagging, rows:[{tier,label,price,base_price,color,bills,pax,
// pax_share,bill_share,rev_per_head,over_price}]} or null (sph.tiers_reason). Tier colours are categorical.
function renderSphGrandTiers(sph) {
  const el = document.getElementById('sphGrandTiers');
  if (!el) return;
  const t = sph && sph.tiers;
  const head = chips => `<div class="flex flex-wrap items-center gap-2 mb-2">
      <h3 class="text-sm font-semibold" style="color:var(--text-primary)">ตาม tier ราคาเมนูใหม่ (ตั้งแต่ 1 ก.ย. 2569)</h3>${chips || ''}</div>`;
  const chip = txt => `<span class="sph-gt-chip text-[11px] px-2 py-0.5 rounded-full" style="background:var(--bg-subtle);color:var(--text-secondary);border:1px solid var(--border)">${txt}</span>`;
  if (!t || !(t.rows || []).length) {
    const msg = sph && sph.tiers_reason === 'before_grand_menu'
      ? 'เมนูใหม่เริ่ม 1 ก.ย. 2569 · เลือกช่วงตั้งแต่ ก.ย. 2569'
      : 'ยังไม่มีข้อมูล tier ของช่วงนี้';
    el.innerHTML = head() + `<p class="sph-gt-empty text-xs py-2" style="color:var(--text-muted)">${msg}</p>`;
    return;
  }
  const rows = t.rows.filter(r => (r.pax || 0) > 0 || (r.bills || 0) > 0);
  const chips = (t.clipped ? chip('นับตั้งแต่ 1 ก.ย. 2569') : '')
    + (t.lagging && t.data_to ? chip('ข้อมูล tier ถึง ' + esc(fmtDateTh(t.data_to))) : '');
  const ph = document.body.classList.contains('ph-on');
  const fs = ph ? 12 : 11;
  const bar = rows.map(r => {
    const pct = r.pax_share || 0;
    if (pct < 0.1) return '';
    const inner = pct > 4 ? `<span style="font-size:${fs}px;font-weight:600;color:${inkOn(r.color)}">${pct.toFixed(1)}%</span>` : '';
    return `<div class="sph-gt-seg" data-tier="${esc(r.tier)}" title="${esc(r.label)} — ${fmtN(r.pax)} ลูกค้า · ${fmtN(r.bills)} บิล (${pct.toFixed(1)}%)"
      style="flex:${pct};background:${r.color};display:flex;align-items:center;justify-content:center;overflow:hidden;cursor:help">${inner}</div>`;
  }).join('');
  const tiles = rows.map(r => {
    const over = r.over_price == null ? null : Math.round(r.over_price);
    const diff = (over == null || over === 0) ? ''
      : over > 0 ? `<div style="font-size:${fs}px;color:var(--good-ink);font-weight:600">+฿${fmtN(over)} จากราคา tier</div>`
      : `<div style="font-size:${fs}px;color:var(--text-muted)">−฿${fmtN(-over)} จากราคา tier</div>`;
    return `<div class="sph-gt-tile flex items-start gap-2 px-2 py-1.5 rounded border border-slate-200" data-tier="${esc(r.tier)}" style="min-height:44px">
      <span style="width:10px;height:10px;border-radius:3px;background:${r.color};flex-shrink:0;margin-top:4px"></span>
      <div class="flex-1 min-w-0">
        <div class="flex items-baseline justify-between gap-1">
          <span class="font-medium truncate" style="font-size:${fs + 1}px;color:var(--text-primary)">${esc(r.label)}</span>
          <span class="font-bold" style="font-size:${fs + 2}px;color:var(--text-primary)">${(r.pax_share || 0).toFixed(1)}%</span>
        </div>
        <div style="font-size:${fs}px;color:var(--text-secondary)">${fmtN(r.pax)} ลูกค้า · ${fmtN(r.bills)} บิล</div>
        <div style="font-size:${fs}px;color:var(--text-secondary)">ยอด/หัว ${r.rev_per_head == null ? '—' : '฿' + fmtN(r.rev_per_head)}</div>
        ${diff}
      </div>
    </div>`;
  }).join('');
  el.innerHTML = head(chips)
    + `<div class="rounded-lg overflow-hidden border border-slate-200 mb-2" style="height:38px;display:flex" id="sphGrandBar">${bar}</div>`
    + `<div id="sphGrandLegend" class="grid grid-cols-2 md:grid-cols-3 gap-2">${tiles}</div>`;
  const barEl = document.getElementById('sphGrandBar');
  _sphGrandFit(barEl);
  if (barEl && typeof ResizeObserver === 'function') new ResizeObserver(() => _sphGrandFit(barEl)).observe(barEl);
}

// A share label stays only where it fits inside its segment (a narrow phone bar hides the label of a ~5 % segment;
// the tile below carries the number and the segment keeps its tooltip).
function _sphGrandFit(barEl) {
  if (!barEl || !barEl.offsetWidth) return;
  barEl.querySelectorAll('.sph-gt-seg').forEach(seg => {
    const l = seg.querySelector('span');
    if (!l) return;
    l.style.visibility = '';
    if (l.offsetWidth > seg.clientWidth - 4) l.style.visibility = 'hidden';
  });
}

async function loadPersona() {
  const data = await api(`${API}/api/analysis/persona${qp()}`);
  const o = data.overall;
  document.getElementById('pAvgParty').textContent  = (o.avg_party_size||0).toFixed(1);
  document.getElementById('pAvgHead').textContent   = fmtN(o.avg_per_head);
  document.getElementById('pAvgTicket').textContent = fmtN(o.avg_ticket);
  document.getElementById('pTxCount').textContent   = fmtN(o.tx_count);

  // Party size chart
  const ps = data.party_sizes;
  mkChart('chartParty', {
    type: 'bar',
    data: {
      labels: ps.map(r => r.label + (r.size>=6?' people+':' '+(r.size===1?'person':'people'))),
      datasets: [{ label:'ใบเสร็จ', data: ps.map(r=>r.tx_count), backgroundColor:'#4a70b0', borderRadius:4 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend:{display:false}, datalabels:{display:false} },
      scales: {
        x: { grid:{display:false}, ticks:{color:'#64748b',font:{size:11}} },
        y: { grid:{color:'#f1f5f9'}, ticks:{color:'#94a3b8',font:{size:10}} }
      }
    }
  });

  // Food/drink/other
  const fd = data.food_drink_split;
  mkChart('chartFdSplit', {
    type: 'doughnut',
    data: {
      labels: ['Food','Drink','Other'],
      datasets: [{ data:[fd.food,fd.drink,fd.other], backgroundColor:['#4a70b0','#10b981','#f59e0b'], borderWidth:1, borderColor:'#fff' }]
    },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: '60%',
      plugins: {
        legend: { position:'bottom', labels:{color:'#64748b',font:{size:11},boxWidth:12,padding:8} },
        datalabels: { display: true, formatter: v => v+'%', color:'#fff', font:{weight:'bold',size:11} },
        tooltip: { callbacks: { label: ctx => ` ${ctx.raw}%` } }
      }
    }
  });

  // Busy hours
  const hrs = data.busy_hours;
  mkChart('chartHours', {
    type: 'bar',
    data: {
      labels: hrs.map(h => h.hour + ':00'),
      datasets: [{ label:'บิล', data: hrs.map(h=>h.tx_count),
                   backgroundColor: hrs.map(h => h.tx_count === Math.max(...hrs.map(x=>x.tx_count)) ? '#ef4444' : '#4a70b0'),
                   borderRadius: 3 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend:{display:false}, datalabels:{display:false} },
      scales: {
        x: { grid:{display:false}, ticks:{color:'#94a3b8',font:{size:9}, maxRotation:45} },
        y: { grid:{color:'#f1f5f9'}, ticks:{color:'#94a3b8',font:{size:9}} }
      }
    }
  });

  // Party size table
  const body = document.getElementById('partyBody');
  body.innerHTML = '';
  ps.forEach(r => {
    body.innerHTML += `<tr class="border-t border-slate-100">
      <td class="py-2 pr-6">${r.label} ${r.size>=6?'or more':r.size===1?'person':'people'}</td>
      <td class="py-2 pr-6 text-right">${fmtN(r.tx_count)}</td>
      <td class="py-2 pr-6 text-right">${fmtN(r.avg_bill)}</td>
      <td class="py-2 text-right font-semibold text-indigo-600">${fmtN(r.avg_per_head)}</td>
    </tr>`;
  });
}


// ════════════════════════════════════════════════════════════════
// TAB 3 — TREND
// ════════════════════════════════════════════════════════════════
let _gpGran = 'daily';
function setGpGran(g){
  _gpGran = g;
  document.querySelectorAll('#gpGranBar button').forEach(b=>{
    if(b.dataset.gran===g){ b.className='px-3 py-1 bg-indigo-600 text-white'; }
    else { b.className='px-3 py-1 text-slate-600 hover:bg-slate-50'; }
  });
  loadGrossProfit();
}

async function loadGrossProfit(){
  const month  = document.getElementById('cmpMonth')?.value;
  const branch = getBranch();
  if(!month) return;
  const qp = new URLSearchParams({ granularity: _gpGran });
  if(branch) qp.set('branch', branch);
  if(_gpGran === 'daily'){
    qp.set('date_from', month + '-01');
    // last day of month
    const [y,m] = month.split('-').map(Number);
    const last = new Date(y, m, 0).getDate();
    qp.set('date_to', month + '-' + String(last).padStart(2,'0'));
  } else if(_gpGran === 'weekly'){
    qp.set('date_from', month + '-01');
    const [y,m] = month.split('-').map(Number);
    const last = new Date(y, m, 0).getDate();
    qp.set('date_to', month + '-' + String(last).padStart(2,'0'));
  } else {
    // monthly: show last 12 months ending at selected month
    const [y,m] = month.split('-').map(Number);
    const from = new Date(y, m-12, 1);
    qp.set('month_from', from.getFullYear()+'-'+String(from.getMonth()+1).padStart(2,'0'));
    qp.set('month_to', month);
  }
  let rows = [];
  try { rows = await api(`${API}/api/analysis/gross_profit?${qp}`); }
  catch(e){ rows = []; }
  if(!Array.isArray(rows)) rows = [];

  const sum = rows.reduce((a,r)=>{
    a.rev  += r.revenue      || 0;
    a.cogs += r.cogs         || 0;
    a.gp   += r.gross_profit || 0;
    return a;
  }, {rev:0,cogs:0,gp:0});
  const pct = sum.rev ? (sum.gp/sum.rev*100) : 0;
  document.getElementById('gpKpiRev').textContent  = fmt(sum.rev);
  document.getElementById('gpKpiCogs').textContent = fmt(sum.cogs);
  document.getElementById('gpKpiGp').textContent   = fmt(sum.gp);
  document.getElementById('gpKpiPct').textContent  = pct.toFixed(1) + '%';

  const labels = rows.map(r=>{
    if(_gpGran === 'daily')   return (r.period||'').slice(8);
    if(_gpGran === 'weekly')  return (r.week_start||r.period||'').slice(5);
    return (r.period||'');  // monthly: 'YYYY-MM'
  });

  mkChart('chartGrossProfit', {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label:'ต้นทุนขาย',         data: rows.map(r=>r.cogs||0),         backgroundColor:'rgba(245,158,11,0.75)', stack:'rev', borderRadius:3 },
        { label:'กำไรขั้นต้น', data: rows.map(r=>r.gross_profit||0), backgroundColor:'rgba(16,185,129,0.85)', stack:'rev', borderRadius:3 },
        { label:'อัตรากำไร %',     data: rows.map(r=>r.margin_pct||0),   type:'line', borderColor:'#4a70b0',
          backgroundColor:'rgba(31,58,107,0.1)', borderWidth:2, pointRadius:2, tension:0.3, yAxisID:'yPct', fill:false },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels:{color:'#64748b',font:{size:11}} },
        datalabels: { display:false },
        tooltip: { callbacks: { label: ctx => {
          const v = ctx.raw;
          if(ctx.dataset.label === 'Margin %') return `Margin %: ${(v||0).toFixed(1)}%`;
          return `${ctx.dataset.label}: ${fmt(v)}`;
        }}}
      },
      scales: {
        x:    { stacked:true, grid:{color:'#f1f5f9'}, ticks:{color:'#94a3b8',font:{size:10}} },
        y:    { stacked:true, position:'left',  grid:{color:'#f1f5f9'}, ticks:{color:'#94a3b8',font:{size:10},callback:v=>fmt(v)} },
        yPct: { position:'right', grid:{display:false}, ticks:{color:'#4a70b0',font:{size:10},callback:v=>v+'%'} },
      }
    }
  });
}

async function loadTrend() {
  // the page opens on its defaults (latest month vs the one before) even when the pickers were not filled yet - a phone's first visit
  if (!document.getElementById('cmpMonth')?.value) { if (allMonths.length) populateCmpDropdowns(); else await loadMonths(); }
  const month = document.getElementById('cmpMonth')?.value;
  const vsSel = /** @type {HTMLSelectElement|null} */ (document.getElementById('cmpVs'));
  // PR-PP2: A follows the top bar's month; a B that is not before it moves to the month before A
  if (vsSel && month && (!vsSel.value || vsSel.value >= month)) {
    const before = [...vsSel.options].map(o => o.value).filter(v => v && v < month).sort().pop();
    if (before) vsSel.value = before;
  }
  const vs    = vsSel?.value;
  if (!month || !vs || month === vs) return;

  const branch = getBranch();
  const qpCmp  = new URLSearchParams({ month, vs });
  if (branch) qpCmp.set('branch', branch);

  const [cmp, daily, dow] = await Promise.all([
    api(`${API}/api/analysis/compare?${qpCmp}`),
    api(`${API}/api/analysis/daily?month=${month}${branch?'&branch='+encodeURIComponent(branch):''}`),
    api(`${API}/api/analysis/dow?month=${month}${branch?'&branch='+encodeURIComponent(branch):''}`),
  ]);

  // Delta KPIs
  const cur = cmp.current, pri = cmp.prior, del = cmp.delta;
  const isMtd = !!cmp.mtd;                       // primary month still partial → MTD-fair
  const mtdTag = isMtd ? ` · MTD 1–${cmp.mtd_cutoff_day}` : '';
  const kpis = [
    { id:'Rev',    cur: fmt(cur.revenue), pri: fmt(pri.revenue), pct: del.revenue },
    { id:'Tx',     cur: fmtN(cur.tx_count),   pri: fmtN(pri.tx_count),   pct: del.tx_count },
    { id:'Cust',   cur: fmtN(cur.customer_count), pri: fmtN(pri.customer_count), pct: del.customer_count },
    { id:'Ticket', cur: fmtN(cur.avg_ticket), pri: fmtN(pri.avg_ticket), pct: del.avg_ticket },
  ];
  kpis.forEach(k => {
    document.getElementById('delta'+k.id+'Current').textContent = k.cur;
    document.getElementById('delta'+k.id+'Prior').textContent   = 'vs ' + k.pri + ' (' + fmtMonth(vs) + mtdTag + ')';
    document.getElementById('delta'+k.id+'Badge').outerHTML =
      `<span id="delta${k.id}Badge">${badge(k.pct)}</span>`;
  });

  // % change horizontal bar chart (avoids Revenue/Tx scale mismatch)
  const pctValues = [del.revenue, del.tx_count, del.customer_count, del.avg_ticket].map(v => v ?? 0);
  const barColors = pctValues.map(v => v >= 0 ? 'rgba(16,185,129,0.75)' : 'rgba(239,68,68,0.75)');
  mkChart('chartCmpBar', {
    type: 'bar',
    data: {
      labels: ['ยอดขาย','บิล','ลูกค้า','ยอดเฉลี่ยต่อบิล'],
      datasets: [{
        label: '% เปลี่ยนแปลงเทียบ ' + fmtMonth(vs),
        data: pctValues,
        backgroundColor: barColors,
        borderRadius: 5,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        datalabels: {
          display: true,
          // a gain is written past the tip of its bar; a fall just right of the zero line - past ITS tip it ran into the category
          // names on a phone (LK)
          anchor: ctx => ctx.dataset.data[ctx.dataIndex] >= 0 ? 'end' : 'start',
          align:  'right',
          formatter: v => (v > 0 ? '+' : '') + v.toFixed(1) + '%',
          color: '#1e293b', font: { size: 12, weight: '600' },
        },
      },
      scales: {
        x: { grid:{color:'#f1f5f9'}, ticks:{color:'#94a3b8',font:{size:10},callback:v=>v+'%'} },
        y: { grid:{display:false}, ticks:{color:'#475569',font:{size:12,weight:'500'}} },
      },
    }
  });

  // Daily chart
  mkChart('chartDaily', {
    type: 'line',
    data: {
      labels: daily.map(d => d.day.slice(8)),
      datasets: [
        { label:'รายได้', data: daily.map(d=>d.revenue), borderColor:'#4a70b0', backgroundColor:'rgba(31,58,107,0.07)',
          borderWidth:2, pointRadius:3, fill:true, tension:0.3, yAxisID:'yRev' },
        { label:'ลูกค้า', data: daily.map(d=>d.customer_count), borderColor:'#10b981',
          borderWidth:1.5, pointRadius:2, tension:0.3, yAxisID:'yCust', borderDash:[4,3] },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend:{labels:{color:'#64748b',font:{size:11}}}, datalabels:{display:false},
                 glance: { dates: daily.map(d => d.day) } },   // bare "01".."31" labels: real dates for weekend bands
      scales: {
        x: { grid:{color:'#f1f5f9'}, ticks:{color:'#94a3b8',font:{size:10}} },
        yRev:  { position:'left',  grid:{color:'#f1f5f9'}, ticks:{color:'#94a3b8',font:{size:10},callback:v=>fmt(v)} },
        yCust: { position:'right', grid:{display:false}, ticks:{color:'#10b981',font:{size:10}} }
      }
    }
  });

  // Day-of-week — Mon-first, per-day colours
  const _dowOrderMon = [1,2,3,4,5,6,0];
  const _dowSorted = _dowOrderMon.map(d => dow.find(r => r.dow === d) || { dow:d, label:'', revenue:0, avg_ticket:0, avg_revenue:0 });
  const _dowMaxRev = Math.max(..._dowSorted.map(r => r.avg_revenue ?? r.revenue ?? 0));
  const _dowColors = _dowSorted.map(r => DOW_COLORS[r.dow] || SEMANTIC.revenue);
  mkChart('chartDow', {
    type: 'bar',
    data: {
      labels: ['จ','อ','พ','พฤ','ศ','ส','อา'],
      datasets: [
        { label:'รายได้เฉลี่ย/วัน', data: _dowSorted.map(d=>d.avg_revenue ?? d.revenue ?? 0), backgroundColor:_dowColors, borderRadius:4, yAxisID:'yRev' },
        { label:'ยอดบิลเฉลี่ย', data: _dowSorted.map(d=>d.avg_ticket ?? 0), type:'line', borderColor:'#1e293b',
          borderWidth:2, pointRadius:4, tension:0.3, yAxisID:'yTicket' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend:{labels:{color:'#64748b',font:{size:11}}}, datalabels:{display:false} },
      scales: {
        x: { grid:{display:false}, ticks:{color:'#64748b',font:{size:12}} },
        yRev:    { position:'left',  grid:{color:'#f1f5f9'}, ticks:{color:'#94a3b8',font:{size:10},callback:v=>fmt(v)} },
        yTicket: { position:'right', grid:{display:false}, ticks:{color:'#f59e0b',font:{size:10},callback:v=>fmtN(v)} }
      }
    }
  });

  // Gross profit panel (uses same month + branch context)
  loadGrossProfit();
}
