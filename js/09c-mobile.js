// ─── 09c-mobile.js — The mobile owner summary (ms* views) and the app BOOT: ensureChartSetup(), init(), waitForCharts() ───
// Slice 3/5 of the former 09-deep-tools.js (split 2026-09-05 by contiguous range, so every
// statement keeps its load order). Shared global scope: index.html loads js/*.js in name order.

// ════════════════════════════════════════════════════════════════
// MOBILE OWNER SUMMARY
// ════════════════════════════════════════════════════════════════
async function showFullDashboard(tab) {
  // PR-D3: on the phone shell the full dashboard is a page like any other (the Daily Brief unless a tab is named)
  if (phOn()) { await phOpenPage(() => { switchTab(tab || (_desktopBooted && activeTab && activeTab !== 'launcher' ? activeTab : 'home')); }); return; }
  document.body.classList.add('force-desktop');
  try { localStorage.setItem('oshinei_phone_mode', 'full'); } catch (e) {}   // next open lands here (init, 03-tabs-main.js)
  window.scrollTo(0, 0);
  // phone-first boot (03-tabs-main.js): the desktop shell loads now, on the first tap
  const first = !_desktopBooted;
  try { showLoadingBar(); } catch (e) {}
  try { await ensureDesktopBooted(); } catch (e) { console.warn('desktop boot:', e); }
  try {
    if (tab) switchTab(tab);
    else if (!first) { if (!activeTab) switchTab('home'); else loadActiveTab(); }
  } catch (e) {}
}
// QA M4 · the link on the phone summary of a supply role: the full shell, opened on หน้างาน
async function msOpenSupply() {
  if (phOn()) { await phOpenPage(() => supplyOpen()); return; }
  await showFullDashboard();
  try { supplyOpen(); } catch (e) { console.warn('supply open:', e); }
}
function showMobileSummary() {
  if (phOn()) { phShowSummary(); return; }        // PR-D3: the summary is a page of the phone shell
  document.body.classList.remove('force-desktop');
  try { localStorage.setItem('oshinei_phone_mode', 'summary'); } catch (e) {}
  window.scrollTo(0, 0);
  loadMobileSummary(true);
}

// ════════════════════════════════════════════════════════════════
// PHONE SHELL (PR-D3, 2026-10-21)
// ════════════════════════════════════════════════════════════════
// On a phone (<= 700 px) every login opens on the DEPARTMENT LAUNCHER (the same registry, DEPARTMENTS in js/01-core.js, that draws the
// desktop's หน้าแรก) with a bottom navigation: หน้าแรก · งานของฉัน · ค้นหา · ฉัน (the branch login: หน้าแรก · ตัดเบิก · สั่งสินค้า ·
// รับสินค้า · ฉัน; the franchise owner: หน้าแรก · ฉัน). Three things sit behind it and open on demand:
//   - the owner's deep summary (#mobileSummary, the ms* views above): the first tile of ผู้บริหาร and the เมื่อวาน figure of the header;
//   - the pages: the full shell (tab fragments + slices, ~2 MB) boots on the FIRST page a person opens, never before;
//   - the launcher's own code: js/18-launcher.js alone (phEnsureLauncher), for the registry helpers deptVisible / deptOpenPage.
// body.ph-on = the shell is in force (set only here, only <= 700 px); body.ph-home = a launcher view is on screen (#phHome) and the
// summary / shell are hidden behind it. A page opens as before (switchTab / supplyGo / _shmOpen), so every deep link still lands.
let _phView = 'home', _phDept = '', _phTodoGroups = [], _phTodoAt = 0, _phTodoSeq = 0, _phSearchList = [];
const _PH_HOME_VIEWS = ['home', 'dept', 'search', 'me'];
// the bottom navigation's own icons (24x24, stroke 1.8 - the DEPT_ICONS style); the rest come from DEPT_ICONS
const _PH_ICON = {
  home: 'M4 10l8-6 8 6v10H4z', search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z M20 20l-3.5-3.5',
  me: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M4 21a8 8 0 0 1 16 0', back: 'M15 5l-7 7 7 7',
};
function _phoneApp() { try { return window.innerWidth > 0 && window.matchMedia('(max-width:700px)').matches; } catch (e) { return false; } }
function _phPath(d, size) { return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`; }
function _phKind() { const r = (window.AUTH && window.AUTH.enabled && window.AUTH.role) || 'management'; return r === 'branch' ? 'branch' : r === 'franchise' ? 'franchise' : 'centre'; }
// js/18-launcher.js alone: deptVisible, deptOpenPage, lnchDateLabel, lnchWho. The other slices wait for the first page.
function phEnsureLauncher() {
  if (typeof deptVisible === 'function') return Promise.resolve();
  if (_slicesP) return _slicesP;                       // the whole load is under way and brings it
  const t = document.querySelector('script.lazy-slice[data-lazy^="/js/18-launcher.js"]');
  return t ? _loadSliceTag(t) : ensureSlicesLoaded();
}
async function phBoot() {
  document.body.classList.add('ph-on', 'ph-home');          // the launcher's place is held while its slice loads: no flash of the summary behind it
  try { await phEnsureLauncher(); } catch (e) { console.warn('launcher slice:', e); }
  try { navBadgesStart(); } catch (e) { /* PR-D4: the red counts need the launcher slice; without it there are none */ }
  phNavRender();
  // a tapped notification: a supply reminder opens its page, an owner's alert opens the summary (which applies the link itself)
  if (_supplyPendingLink) { const l = _supplyPendingLink; _supplyPendingLink = ''; await phOpenPage(() => supplyDeepLink(l)); return; }
  // PR-F4: a franchise-message notification: head office opens its inbox (the owner's link opens ร้านของฉัน below)
  if (_fmsgPending && _fmsgPending.go === 'fmsg' && tabAllowed('fmsg')) { await phOpenPage(() => switchTab('fmsg')); return; }
  if (_msPendingLink && hasModule('report.management')) { await phShowSummary(); return; }
  if (_phKind() === 'franchise') { await phOpenFranchise(); return; }
  phShowHome();
}
function phRetryBoot() { const b = document.getElementById('phRetry'); if (b) b.disabled = true; phBoot(); }
// PR-F1: the franchise owner's หน้าแรก IS ร้านของฉัน - the login opens on the page itself (no launcher of one tile); the bottom
// navigation keeps หน้าแรก (this page) and ฉัน
// phone B (2026-10-31): หน้าแรก while a detail screen of ร้านของฉัน is open goes back to its cards (frcHome, js/17-franchise.js)
function phOpenFranchise() { _phDept = ''; return phOpenPage(() => { switchTab('franchise'); if (typeof frcHome === 'function') frcHome(); }); }
// the width crossed 700 px (a turned phone, a dragged window): the shell comes or goes
function phViewportChanged() {
  if (_phoneApp()) { if (!phOn()) { phBoot(); periodChipRole(); branchChipRender(); } return; }
  if (!phOn()) return;
  phPeriodClose(); phBranchClose();
  document.body.classList.remove('ph-on', 'ph-home');
  periodChipRole(); branchChipRender();
  phNavRender();
  if (!_desktopBooted) ensureDesktopBooted(); else if (activeTab === 'launcher' || !activeTab) switchTab(_landingTab());
}
function phForeground() {
  const stale = (t) => !t || Date.now() - t > _STALE_MS;
  if (_phView === 'summary') { if (_msFetchedAt && stale(_msFetchedAt)) msRefresh(); }
  else if (_phView === 'home') { if (stale(_phTodoAt)) phRenderHome(); }
  else if (_phView === 'page') { if (_desktopBooted && _lastTabLoadAt && stale(_lastTabLoadAt)) loadActiveTab(); }
}
function _phSetView(v) {
  _phView = v;
  const cl = document.body.classList;
  cl.toggle('ph-home', _PH_HOME_VIEWS.includes(v));
  if (v === 'summary') cl.remove('force-desktop'); else if (v === 'page') cl.add('force-desktop');
  cl.toggle('ph-detail', v === 'summary');       // phone B (owner 2026-10-30): a detail screen of the summary - one header (#phTop), the shell's one bottom bar
  if (v !== 'summary') { try { msCloseSheet(); } catch (e) { /* the sheet is not open */ } }
  phPeriodClose(); phBranchClose();
  if (_PH_HOME_VIEWS.includes(v)) document.body.dataset.group = 'home';
  _PH_HOME_VIEWS.forEach(x => { const el = document.getElementById('phv-' + x); if (el) el.hidden = x !== v; });
  phNavRender();
  phFitApply();                                   // PR-MA: พอดีจอ is for a page; a launcher view or the summary is always the touch layout
  window.scrollTo(0, 0);
  const ca = document.querySelector('.content-area'); if (ca) ca.scrollTop = 0;
}
// a screen reader follows the view change: the new view's heading takes the focus (it is not a tab stop)
function _phFocusHead(v) {
  const h = (document.body.classList.contains('ph-centre') && document.getElementById('phTopT')) || document.querySelector('#phv-' + v + ' h1');
  if (h) { h.setAttribute('tabindex', '-1'); try { h.focus({ preventScroll: true }); } catch (e) { /* a hidden view takes no focus */ } }
}
function _phBusy(on) {
  const el = document.getElementById('phBusy'); if (el) el.hidden = !on;
  try { on ? showLoadingBar() : hideLoadingBar(); } catch (e) { /* no loading bar yet */ }
}
// Open a page: the launcher stays on screen (with "กำลังเปิดหน้า…") until the shell has booted, then the page replaces it.
async function phOpenPage(fn) {
  if (!_desktopBooted) {
    _phBusy(true);
    try { await ensureDesktopBooted({ noLanding: true }); }
    catch (e) {
      console.warn('desktop boot:', e); _desktopBootP = null; _phBusy(false);
      try { notifyError('เปิดหน้าไม่สำเร็จ ตรวจสอบการเชื่อมต่อแล้วลองใหม่'); } catch (e2) { /* no toast yet */ }
      return;
    }
    _phBusy(false);
  }
  _phSetView('page');
  try { fn(); } catch (e) { console.warn('phone page:', e); }
}
async function phShowSummary() {
  // phone B (owner 2026-10-30): a centre login's summary IS หน้าแรก - the overview cards under the hero; a card opens its detail screen
  if (_phKind() === 'centre') {
    phShowHome();
    if (!_msPendingLink) setTimeout(() => { const ov = document.getElementById('phOv'); if (ov) ov.scrollIntoView({ block: 'start' }); }, 0);
    return;
  }
  _phSetView('summary');
  msShowView(_msView);   // the view on screen is the one the report will draw into
  if (!window.__dataLastDate) { try { await initDataCoverageNote(); } catch (e) { /* the note is non-critical */ } }
  loadMobileSummary(true);
}

// ── the bottom navigation ──────────────────────────────────────────────────────────────────────────────
// PR-MA (owner 2026-10-24): one bar per role, built for its daily work - หน้าแรก, up to three pages, ฉัน. The red count of an entry is the
// number of tasks waiting on that page (nav_badges, data-nb="p:<tab>:<pane>"); หน้าแรก carries the person's whole open list (data-nb="t", the
// count the old งานของฉัน entry had). ค้นหา moved to the magnifier of the header. A page the role may not open is left out of its bar.
// `home` = the page หน้าแรก opens for the role (else the launcher); a second tap on หน้าแรก while that page is open goes to the launcher.
const _PH_BARS = {
  // PR-PD (owner 2026-10-29, no. 15): a management login's entries open the summary's own detail screens (`ms`), not the desktop pages
  // shrunk to fit; a login without the summary keeps the pages. Phone B (owner 2026-10-30): หน้าแรก · ยอดขาย · สาขา · เมนู · ฉัน - กำไร and
  // ส่วนลด are cards of หน้าแรก (phOvRender)
  management: [{ label: 'ยอดขาย', tab: 'dailysales', ms: 'sales', icon: 'cal' }, { label: 'สาขา', tab: 'overall', ms: 'branches', icon: 'store' }, { label: 'เมนู', tab: 'menu', ms: 'menu', icon: 'book' }],
  salessupport: [{ label: 'คิวใบสั่งซื้อ', tab: 'orderhub', pane: 'queue', icon: 'inbox', nb: true }, { label: 'ใบตัดเบิกสาขา', tab: 'orderhub', pane: 'issues', icon: 'doc', nb: true },
    { label: 'เคลม', tab: 'orderhub', pane: 'claims', icon: 'ret', nb: true }],
  warehouse: [{ label: 'คิวใบสั่งซื้อ', tab: 'orderhub', pane: 'queue', icon: 'inbox', nb: true }],
  analyst: [{ label: 'ต้นทุนต่อจาน', tab: 'bomcenter', pane: 'dishcost', icon: 'coin', nb: true }, { label: 'ราคารายเดือน', tab: 'bomcenter', pane: 'price', icon: 'tag', nb: true },
    { label: 'ไฟล์ราคา', tab: 'bomcenter', pane: 'pricefile', icon: 'upload', nb: true }],
  accounting: [{ label: 'ใบสรุปตัดเบิก', tab: 'bomcenter', pane: 'issue', icon: 'doc', nb: true }, { label: 'P&L รายสาขา', tab: 'pnlbranch', icon: 'bar' },
    { label: 'GP รายสาขา', tab: 'gpbranch', icon: 'coin' }],
  operation: [{ label: 'ใบตัดเบิกสาขา', tab: 'orderhub', pane: 'issues', icon: 'doc', nb: true }, { label: 'ผลต่างตัดเบิกรายวัน', tab: 'orderhub', pane: 'issuevar', icon: 'diff', nb: true },
    { label: 'ส่วนลด', tab: 'discounts', icon: 'pct' }],
  marketing: [{ label: 'ลูกค้าและบุฟเฟ่ต์', tab: 'buffetperf', icon: 'users' }, { label: 'วิเคราะห์เมนู', tab: 'menu', icon: 'book' }, { label: 'เสียงลูกค้า', tab: 'feedback', icon: 'msg' }],
  innovation: [{ label: 'ทะเบียนวัตถุดิบ', tab: 'bomcenter', pane: 'ing', icon: 'db' }],
};
// 2026-10-31: marketing's หน้าแรก is its own department home (เสียงลูกค้า), no longer the chain's สรุปประจำวัน
const _PH_HOME_TAB = { salessupport: 'supplyhome', analyst: 'supplyhome' };
function _phRole() { return (window.AUTH && window.AUTH.enabled && window.AUTH.role) || 'management'; }
// may this login open the page? the launcher's own rule when its slice is loaded (deptPageAllowed: the tab, stock mode, the supply item's roles)
function _phPageOk(p) {
  try { return typeof deptPageAllowed === 'function' ? deptPageAllowed(p) : tabAllowed(p.tab); } catch (e) { return false; }
}
function _phHomeTab() { const t = _PH_HOME_TAB[_phRole()]; return t && _phPageOk({ tab: t }) ? t : ''; }
function _phNavItems() {
  const kind = _phKind();
  const home = { id: 'home', label: 'หน้าแรก', path: _PH_ICON.home, js: 'phShowHome()' };
  const me = { id: 'me', label: 'ฉัน', path: _PH_ICON.me, js: 'phOpenMe()' };
  if (kind === 'franchise') return [Object.assign({}, home, { nb: 'p:franchise' }), me];     // PR-F4: the unread replies of head office
  if (kind === 'branch') {
    const b = (id, label, icon, pane) => ({ id, label, path: DEPT_ICONS[icon], js: `phOpenPage(()=>supplyGo({tab:'branchstock',pane:'${pane}'}))`, tab: 'branchstock', pane, nb: `p:branchstock:${pane}` });
    return [home, b('issue', 'ตัดเบิก', 'box', 'issue'), b('order', 'สั่งของ', 'cart', 'order'), b('recv', 'รับของ', 'recv', 'recv'), me];
  }
  const ht = _phHomeTab();
  const ms = typeof hasModule === 'function' && hasModule('report.management');
  const pages = (_PH_BARS[_phRole()] || _PH_BARS.management).filter(p => (ms && p.ms) || _phPageOk(p)).map(p => {
    const key = p.tab + (p.pane ? ':' + p.pane : '');
    if (ms && p.ms) return { id: 'ms-' + p.ms, label: p.label, path: DEPT_ICONS[p.icon], js: `phSummaryGo('${p.ms}')`, tab: null, pane: null, nb: '' };
    const go = p.pane ? `supplyGo({tab:'${p.tab}',pane:'${p.pane}'})` : `switchTab('${p.tab}')`;
    return { id: 'pg-' + key, label: p.label, path: DEPT_ICONS[p.icon], js: `phBarGo(()=>${go})`, tab: p.tab, pane: p.pane || null, nb: p.nb ? 'p:' + key : '' };
  });
  return [Object.assign({}, home, { nb: 't', tab: ht || null, js: 'phHomeTap()' }), ...pages, me];
}
// หน้าแรก: the role's home page (หน้างาน, สรุปประจำวัน) or the launcher; tapped again on that page, the launcher
function phHomeTap() {
  if (_phView === 'summary' && _phDetailPushed) { history.back(); return; }   // phone B: a detail screen - its history entry goes too (popstate shows the cards)
  const ht = _phHomeTab();
  if (ht && !(_phView === 'page' && activeTab === ht)) { _deptCur = null; _deptFocus = null; phOpenPage(() => ht === 'supplyhome' ? supplyGo({ tab: 'supplyhome' }) : switchTab(ht)); return; }
  phShowHome();
}
function phBarGo(fn) { _deptCur = null; _deptFocus = null; phOpenPage(fn); }
// PR-PD no. 15 · a bottom-bar entry that is one of the summary's tabs: open the summary on that tab
// 2026-10-29: the asked view is shown at once - before, a first open (no report yet) left the สรุป view on screen while the
// report drew into the hidden ยอดขาย / กำไร / สาขา view, so the owner saw an empty summary
// phone B (owner 2026-10-30): the entry opens that detail screen (with a back to the cards of หน้าแรก)
async function phSummaryGo(v) { _deptCur = null; _deptFocus = null; phOpenDetail(v); }
function _phNavOn(it) {
  if (_phView === 'search' || _phView === 'me') return it.id === _phView;
  if (_phView === 'summary') { const id = 'ms-' + _msView; return _phNavItems().some(x => x.id === id) ? it.id === id : it.id === 'home'; }   // a card's detail lights หน้าแรก
  if (_phView !== 'page') return it.id === 'home';
  const acc = typeof _SUPPLY_PANE === 'object' ? _SUPPLY_PANE[activeTab] : null, pane = acc ? acc.get() : null;
  const hit = x => x.tab && x.tab === activeTab && (!x.pane || x.pane === pane);
  const items = _phNavItems();
  // the entry of the open page lights up (หน้าแรก for its own home page); any other page lights หน้าแรก
  if (it.id === 'home') return hit(it) || !items.some(x => x.id !== 'home' && hit(x));
  return hit(it);
}
function phNavRender() {
  phTopRender();                                  // phone B: the one header follows every view and page switch
  const nav = document.getElementById('phNav'); if (!nav) return;
  if (!phOn()) { nav.innerHTML = ''; return; }
  const items = _phNavItems();
  nav.style.gridTemplateColumns = `repeat(${items.length}, minmax(0, 1fr))`;
  nav.innerHTML = items.map(it => { const on = _phNavOn(it);
    return `<button type="button" class="ph-nav-i${on ? ' active' : ''}" aria-current="${on ? 'page' : 'false'}"${it.nb ? ` data-nb="${it.nb}"` : ''} onclick="${it.js}">${_phPath(it.path, 24)}<span>${it.label}</span></button>`; }).join('');
  try { navBadgesApply(); } catch (e) { /* the launcher slice is not loaded yet */ }
}

// ── หน้าแรก ──────────────────────────────────────────────────────────────────────────────────────────────
function phShowHome() { if (_phKind() === 'franchise') { phOpenFranchise(); return; } _phDept = ''; _phSetView('home'); phRenderHome(); }
function phRenderHome() {
  const host = document.getElementById('phv-home'); if (!host) return;
  const kind = _phKind();
  if (kind === 'franchise') { phOpenFranchise(); return; }            // PR-F1: no launcher of one tile
  if (typeof deptVisible !== 'function') {
    // audit 2026-10: js/18-launcher.js did not load (a dropped connection) - the home views need its deptVisible / lnchDateLabel, so
    // a retry button instead of a ReferenceError and a blank screen
    host.innerHTML = `<div class="ph-body" style="padding:24px 16px;text-align:center"><p class="ph-empty">โหลดหน้าแรกไม่สำเร็จ</p>`
      + `<button type="button" class="fm-btn fm-primary" id="phRetry" style="min-height:44px;padding:0 20px" onclick="phRetryBoot()">โหลดไม่สำเร็จ · ลองใหม่</button></div>`;
    return;
  }
  host.innerHTML = kind === 'branch' ? _phBranchHome() : _phCentreHome();
  try { navBadgesApply(); navBadgesRefresh(false); } catch (e) { /* PR-D4: no launcher slice, no counts */ }
  phLoadTasks();
  if (kind === 'centre' && hasModule('sales.overview') && !_phDeptHeroKey()) phLoadYesterday();
  if (kind === 'centre' && hasModule('report.management')) phOvLoad();     // phone B: the overview cards
  if (kind === 'centre') phTileDataLoad();                                   // item 6: the figures of the tiles (the hero's own answers)
}
// U2: the header shows the name a person recognises (display name, else the Thai role label), never the login ID
function _phUserId() { const a = window.AUTH || {}; return a.enabled ? (typeof lnchWho === 'function' ? lnchWho() : (a.display_name || '')) : ''; }
// PR-MA: ค้นหา is the magnifier of the header (top right, beside the avatar); the avatar opens ฉัน
function _phInitial() { return (_phUserId() || 'ฉ').trim().charAt(0).toUpperCase() || 'ฉ'; }
// phone B (owner 2026-10-30): the hero sits under the one header (#phTop: the title, the chips, ฉัน) - the date and the magnifier, then the figure
function _phHero(inner, label) {
  return `<section class="ph-hero" aria-label="${esc(label || 'เมื่อวาน')}"><div class="ph-hero-top"><span class="ph-hero-date">${esc(lnchDateLabel())}</span>`
    + `<button type="button" class="ph-hsearch" id="phHeroSearch" onclick="phOpenSearch()" aria-label="ค้นหาหน้า">${_phPath(_PH_ICON.search, 22)}</button></div>${inner}</section>`;
}
// the phone home grid: the person's own department(s) (js/18-launcher.js deptHome; management sees all)
function _phDeptTiles(rows) {
  return rows.map(r => { const d = r.dept, st = _phDeptChip(r);
    // phone review item 6: ONE chip per department - pages to look at (red / amber) or ✓; none while nothing has answered (the red badge then)
    const chip = !st ? `<span class="ph-cnt" data-dept="${d.key}" hidden></span>`
      : `<b class="ph-ts ph-ts-${st.tone} ph-dept-st" aria-label="${st.n ? `ต้องดู ${fmtN(st.n)} หน้า` : 'เรียบร้อย'}">${st.n ? fmtN(st.n) : '✓'}</b>`;
    return `<a href="#" class="ph-dept" data-dept="${d.key}" style="--dk:var(--dept-${d.hue}-ink);--dt:var(--dept-${d.hue}-tint);--df:var(--dept-${d.hue}-f);--df2:var(--dept-${d.hue}-f2)" onclick="phOpenDept('${d.key}');return false">`
      + `<span class="ph-badge">${_deptSvg(d.icon, 26)}</span><span class="ph-dept-n">${esc(d.label)}</span>${chip}</a>`; }).join('');
}
function _phCentreHome() {
  const dk = _phDeptHeroKey();
  if (dk) return _phDeptHome(dk);                     // 2026-10-31: a department of its own work, not the chain's sales
  const vis = deptHome();
  // phone B: for the owner the hero IS the full-width เมื่อวาน card of the overview - a tap opens its detail (the day + every branch)
  const yest = hasModule('sales.overview')
    ? `<button type="button" class="ph-yest" id="phYest" onclick="${hasModule('report.management') ? "phOpenDetail('yesterday')" : "phOpenPage(() => switchTab('home'))"}" aria-label="ยอดขายเมื่อวาน ${hasModule('report.management') ? 'เปิดรายละเอียดเมื่อวาน' : 'เปิดสรุปประจำวัน'}"><span class="ph-yest-l" id="phYestL">ยอดขายเมื่อวาน</span><span class="ph-yest-v" id="phYestV">…</span><span class="ph-yest-s" id="phYestS"></span>`
      + `<span class="ph-yest-c" id="phYestCause" hidden></span><span class="ph-yest-t" id="phYestTrust" hidden></span><span class="ph-yest-p" id="phYestPend" hidden></span></button>` : '';
  return _phHero(yest)
    + `<a href="#" class="ph-taskcard" id="phTaskCard" onclick="phOpenTasks();return false"><span class="ph-dot" id="phTaskDot" hidden></span>`
    + `<span class="ph-taskcard-t" id="phTaskText">งานของฉันวันนี้</span><span class="ph-taskcard-go">ดู ›</span></a>`
    + (hasModule('report.management') ? '<section class="ph-body ph-ov" id="phOv" aria-labelledby="phOvH"><div class="ph-ov-h"><h2 class="ph-sec" id="phOvH">ภาพรวม</h2><span class="ph-ov-st" id="phOvSt"></span></div>'
      + '<div class="ph-ov-grid" id="phOvGrid">' + uiSkel(4, 'kpi') + '</div></section>' : '')
    + (vis.length === 1 && _deptRole() !== 'management'
      // one department of its own: its pages straight on the home screen (no lone department tile to tap first)
      // phone review item 6: the tiles carry their status, work first; the pages with nothing to show fold under หน้าอื่น (n)
      ? `<section class="ph-body" id="phDtOne" data-dk="${vis[0].dept.key}" aria-labelledby="phDeptH" style="--dk:var(--dept-${vis[0].dept.hue}-ink)">${_phDtHtml(vis[0].dept)}</section>`
      : `<section class="ph-body" aria-labelledby="phDeptH"><h2 class="ph-sec" id="phDeptH">${_deptRole() === 'management' ? 'แผนก' : 'แผนกของฉัน'}</h2>`
        + (vis.length ? `<div class="ph-depts" id="phDeptGrid">${_phDeptTiles(vis)}</div>` : '<p class="ph-empty">บัญชีนี้ยังไม่มีหน้าที่เปิดได้</p>')
        + '</section>');
}
// ── หน้าแรก of a department (owner 2026-10-31, docs/UI_REVIEW_MOBILE_2026-10-31.md items 4 + 6) ───────────────────────────────────
// Every HQ department answers "วันนี้ต้องทำอะไร" for ITSELF: the hero is its own headline number + one line + the one page to open
// (`hero` of GET /api/nav/badges, bom_center/dept_hero.py - the same open list as the red badges and หน้างาน). The chain sales hero stays
// for report.management and operation only; branch and franchise keep their own screens. Under the hero: งานของฉัน, then the department's
// pages - the ones with work first, each with ONE count or status (red overdue · amber waiting · green nothing open); a page with no status
// folds under "หน้าอื่น (n)". The other departments the role may open come last, and not at all for a role with only its own.
const _PH_DEPT_HERO = { warehouse: 'wh', salessupport: 'ss', accounting: 'acc', analyst: 'ana', innovation: 'kit', marketing: 'mkt' };
function _phDeptHeroKey() {
  if (typeof hasModule === 'function' && hasModule('report.management')) return '';
  const k = _PH_DEPT_HERO[_phRole()] || '';
  return k && typeof DEPARTMENTS === 'object' && DEPARTMENTS.some(d => d.key === k) ? k : '';
}
function _phDeptHome(dk) {
  const d = DEPARTMENTS.find(x => x.key === dk);
  const others = deptVisible().filter(r => !r.home);
  return _phHero(`<div id="phDh">${_phDhHtml(d)}</div>`, `งานของ${d.label}วันนี้`)
    + `<a href="#" class="ph-taskcard" id="phTaskCard" onclick="phOpenTasks();return false"><span class="ph-dot" id="phTaskDot" hidden></span>`
    + `<span class="ph-taskcard-t" id="phTaskText">งานของฉันวันนี้</span><span class="ph-taskcard-go">ดู ›</span></a>`
    + `<section class="ph-body" id="phDt" aria-labelledby="phDeptH" style="--dk:var(--dept-${d.hue}-ink)">${_phDtHtml(d)}</section>`
    + (others.length ? `<section class="ph-body ph-others" aria-labelledby="phOthH"><h2 class="ph-sec" id="phOthH">แผนกอื่น</h2><div class="ph-depts">${_phDeptTiles(others)}</div></section>` : '');
}
// the page a hero opens: one this login may open, else nothing (the button then opens งานของฉัน); its name is _phPageLabel's (ฉัน, below)
function _phGoOk(go) {
  return !!(go && go.tab && tabAllowed(go.tab) && (!go.pane || _phPageOk({ tab: go.tab, pane: go.pane })));
}
function _phDhHtml(d) {
  const h = typeof _NB === 'object' ? _NB.hero : null;
  if (!h || h.dept !== d.key) {
    return `<div class="ph-yest ph-dh" aria-busy="true"><span class="ph-yest-l">${esc(d.label)}</span><span class="ph-yest-v">…</span></div>`;
  }
  const zero = h.n == null;
  const go = _phGoOk(h.go) ? h.go : null, pl = go ? _phPageLabel(go.tab, go.pane).label : '';
  const act = go ? `เปิด${pl || 'หน้างาน'}` : 'ดูงานของฉัน';
  const value = zero ? '<span class="ph-dh-ok">ไม่มีงานค้าง</span>'
    : `${fmtN(h.n)}${h.unit ? ` <small>${esc(h.unit)}</small>` : ''}${h.tone === 'bad' ? ' <span class="ph-cz ph-cz-bad">เร่งด่วน</span>' : ''}`;
  const lbl = zero ? d.label : `${d.label} · ${h.label}`;
  return `<button type="button" class="ph-yest ph-dh" id="phDhBtn" data-tone="${esc(h.tone || '')}" onclick="phDeptHeroGo()" aria-label="${esc(lbl)} ${esc(zero ? 'ไม่มีงานค้าง' : fmtN(h.n) + ' ' + (h.unit || ''))} ${esc(act)}">`
    + `<span class="ph-yest-l">${esc(lbl)}</span><span class="ph-yest-v">${value}</span>`
    + (h.line ? `<span class="ph-dh-line">${esc(h.line)}</span>` : '')
    + `<span class="ph-dh-act">${esc(act)} ›</span></button>`;
}
function phDeptHeroGo() {
  const h = typeof _NB === 'object' ? _NB.hero : null, go = h && _phGoOk(h.go) ? h.go : null;
  if (!go) { phOpenTasks(); return; }
  _deptCur = null; _deptFocus = null;
  phOpenPage(() => (go.pane ? _shmOpen(go) : switchTab(go.tab)));
}
// a page's status for its tile: {n, tone} for open work (the badge count) or a figure waiting on another department ({info}), {tone:'good'}
// where the department's duties are all done, null = no status (folded under หน้าอื่น).
// Phone review item 6 (owner 2026-10-31): a sales page carries its figure ({v, tone, l}: ส่วนลด "1.1%" green under the ceiling, red over; GP
// "45%"; ยอดขายรายวัน the chain's yesterday against its normal) - read from what the home already fetched (_phSalesVals), never a call of its own.
function _phTileState(h, k) {
  if (!h) return null;
  const v = (h.vals || {})[k];
  const p = (h.pages || {})[k];
  if (p && p.n) return { n: p.n, tone: p.tone === 'bad' ? 'bad' : 'watch' };
  if (v) return v;
  const i = (h.info || {})[k];
  if (i) return { n: i, tone: 'watch', info: true };
  return (h.known || []).includes(k) ? { tone: 'good' } : null;
}
// ── the figures of the sales pages for their tiles (item 6): GET /api/home/brief + /api/home/branch-scorecard - the two answers the
// sales hero of management and operation already loads (_msBriefCache[''], _msScorecard; both ttl_cache on the server). No other call.
const _PH_DISC_CEIL = 2.5;                         // the discount ceiling of the chain (% of sales; reports audit 2026-07)
let _phBriefJ = null, _phScJ = null, _phTileWant = false;
function phTileDataLoad() {
  if (_phDeptHeroKey() || !hasModule('sales.overview')) return;
  _phTileWant = true;
  if (!_msBriefCache['']) _msBriefCache[''] = api(`${API}/api/home/brief`);
  if (!_msScorecard) _msScorecard = api(`${API}/api/home/branch-scorecard`);
  Promise.allSettled([_msBriefCache[''], _msScorecard]).then(([b, c]) => {
    let changed = false;
    if (b.status === 'fulfilled') { if (b.value !== _phBriefJ) { _phBriefJ = b.value; changed = true; } } else delete _msBriefCache[''];
    if (c.status === 'fulfilled') { if (c.value !== _phScJ) { _phScJ = c.value; changed = true; } } else _msScorecard = null;
    if (changed || !_phBriefJ) phTilesApply();
  });
}
const _phPctTxt = p => `${p > 0 ? '▲' : p < 0 ? '▼' : '±'}${Math.abs(p).toFixed(Math.abs(p) >= 10 ? 0 : 1)}%`;
function _phSalesVals() {
  const v = {}, b = _phBriefJ, sc = _phScJ;
  const ch = sc && sc.chain;
  // owner 2026-10-31: grey while within the normal band (rules.BELOW_NORMAL_PCT −8 … ABOVE_NORMAL_PCT +10), red only below it, green only above
  if (ch && ch.vs_normal_pct != null) v.dailysales = { v: _phPctTxt(ch.vs_normal_pct), tone: ch.vs_normal_pct <= -8 ? 'bad' : ch.vs_normal_pct >= 10 ? 'good' : 'neu', l: 'ยอดขายเมื่อวานเทียบปกติ' };
  if (sc && Array.isArray(sc.branches) && sc.branches.length) {
    const n = sc.below_count != null ? sc.below_count : belowNormalRows(sc).length;
    v.home = n ? { v: `${fmtN(n)} สาขา`, tone: 'bad', l: 'สาขาต่ำกว่าปกติเมื่อวาน' } : { tone: 'good', l: 'ทุกสาขาอยู่ในเกณฑ์ปกติ' };
    // the chain's discount yesterday: the branches' discount % weighted by their sales (the scorecard's own figure per branch)
    const rows = sc.branches.filter(r => r.reported !== false && r.discount_pct != null && r.revenue > 0);
    const rev = rows.reduce((t, r) => t + r.revenue, 0);
    if (rev > 0) {
      const d = rows.reduce((t, r) => t + r.discount_pct * r.revenue, 0) / rev;
      v.discounts = { v: d.toFixed(1) + '%', tone: d > _PH_DISC_CEIL ? 'bad' : 'good', l: `ส่วนลดเมื่อวาน เพดาน ${_PH_DISC_CEIL}%` };
    }
  }
  const f = b && b.food_cost_pct_mtd;
  if (f != null) {
    const t = (b.cost_health && b.cost_health.food_cost_target) || 58, ok = f <= t;
    v.cost = { v: f.toFixed(1) + '%', tone: ok ? 'good' : 'bad', l: `ต้นทุนอาหารเดือนนี้ เป้าไม่เกิน ${t}%` };
    v.gpbranch = { v: (100 - f).toFixed(1) + '%', tone: ok ? 'good' : 'bad', l: 'GP เดือนนี้' };   // one decimal, the same as ต้นทุน (owner 2026-10-31)
  }
  return v;
}
// the state behind a department's tiles: the department hero (its own tasks) or, for the rest, the red badges of its pages (_NB.pages, the
// branch messages) + the sales figures. null = nothing answered yet: every page shows, nothing folds.
function _phDeptState(d) {
  const hero = typeof _NB === 'object' && _NB.hero && _NB.hero.dept === d.key ? _NB.hero : null;
  const vals = _phSalesVals();
  const nbOk = typeof _NB === 'object' && _NB.at > 0;
  if (!hero && !nbOk && !Object.keys(vals).length) return null;
  if (!hero && _phTileWant && !_phBriefJ && !_phScJ) return null;      // the sales figures are on their way: no fold yet
  const pages = {};
  if (!hero && nbOk) d.pages.forEach(p => { const n = navPageCount(p); if (n) pages[p.tab + (p.pane ? ':' + p.pane : '')] = { n, tone: 'watch' }; });
  return Object.assign({ pages, info: {}, known: [] }, hero || {}, { vals });
}
// the page tiles of a department, work first; o = { h: the state (default: the department's own), head: false = no heading, first: a tile drawn
// ahead of the rest (the owner's summary) }. The phone's department pages, its หน้าแรก and the operation's home grid all draw through here.
// owner 2026-10-31: these pages stay on top of the ผู้บริหาร grid even though they carry no figure (never folded under หน้าอื่น)
const _PH_TILE_PIN = new Set(['mgtreport', 'overall', 'trend', 'pnl', 'pnlbranch']);
function _phDtHtml(d, o) {
  o = o || {};
  const row = deptVisible().find(r => r.dept.key === d.key);
  const pages = row ? row.pages : [];
  const h = 'h' in o ? o.h : _phDeptState(d);
  const tiles = pages.map(x => ({ x, st: _phTileState(h, x.p.tab + (x.p.pane ? ':' + x.p.pane : '')) }));
  const pinned = t => !t.st && _PH_TILE_PIN.has(t.x.p.tab);
  const rank = t => !t.st ? (pinned(t) ? 4 : 5) : t.st.tone === 'bad' ? 0 : t.st.n && !t.st.info ? 1 : t.st.n || t.st.tone === 'watch' ? 2 : 3;
  tiles.sort((a, b) => (rank(a) - rank(b)) || (((b.st && b.st.n) || 0) - ((a.st && a.st.n) || 0)) || (a.x.i - b.x.i));
  const tile = t => {
    const st = t.st;
    const chip = !st ? '' : st.n ? `<b class="ph-ts ph-ts-${st.tone}" aria-label="${st.info ? 'รอ' : 'ค้าง'} ${fmtN(st.n)}">${fmtN(st.n)}</b>`
      : st.v ? `<b class="ph-ts ph-ts-${st.tone === 'bad' ? 'bad' : st.tone === 'watch' ? 'watch' : st.tone === 'neu' ? 'neu' : 'good'}" aria-label="${esc(st.l || '')} ${esc(st.v)}">${esc(st.v)}</b>`
      : `<b class="ph-ts ph-ts-good" role="img" aria-label="${esc(st.l || 'เรียบร้อย')}">✓</b>`;     // a check, not a word: the page name keeps the room
    // a tile with no status keeps the red badge of the launcher (navBadgesApply), as before the answers came
    return `<a href="#" class="ph-ptile"${st ? '' : ` data-nb="${_navPageSpec(t.x.p)}"`} onclick="phOpenDeptPage('${d.key}',${t.x.i});return false">${_deptSvg(t.x.p.icon, 28)}${chip ? `<span class="ph-pt-t"><span>${esc(t.x.p.label)}</span>${chip}</span>` : `<span>${esc(t.x.p.label)}</span>`}</a>`;
  };
  const head = o.head === false ? '' : `<h2 class="ph-sec" id="phDeptH">${esc(d.label)}</h2>`;
  const first = o.first || '';
  // no answer yet (or none for this login): every page as before, no status and nothing folded
  if (!h) return head + `<div class="ph-ptiles">${first}${tiles.map(tile).join('')}</div>`;
  const shown = tiles.filter(t => t.st || pinned(t)), rest = tiles.filter(t => !t.st && !pinned(t));
  return head + (shown.length || first ? `<div class="ph-ptiles">${first}${shown.map(tile).join('')}</div>` : '')
    + (rest.length ? `<details class="ph-more"${shown.length || first ? '' : ' open'}><summary>หน้าอื่น (${fmtN(rest.length)})</summary><div class="ph-ptiles">${rest.map(tile).join('')}</div></details>` : '');
}
// a department tile of the home grid (management's แผนก): ONE chip - how many of its pages need a look (red when one is over its line, amber
// for open work), a green check when every page with a figure is fine, nothing while nothing answered
function _phDeptChip(r) {
  const h = _phDeptState(r.dept);
  if (!h) return null;
  const sts = r.pages.map(x => _phTileState(h, x.p.tab + (x.p.pane ? ':' + x.p.pane : ''))).filter(Boolean);
  if (!sts.length) return null;
  const bad = sts.filter(s => s.tone === 'bad').length, watch = sts.filter(s => s.tone !== 'bad' && (s.n || s.tone === 'watch')).length;
  return bad ? { n: bad + watch, tone: 'bad' } : watch ? { n: watch, tone: 'watch' } : { tone: 'good' };
}
// redraw the hero and the tiles in place when an answer arrives (the badges: js/18-launcher.js navBadgesRefresh; the sales figures:
// phTileDataLoad); a fold keeps its state
function _phKeepFold(host, html) {
  const fold = host.querySelector('details.ph-more'), wasOpen = fold ? fold.open : null;
  host.innerHTML = html;
  const again = host.querySelector('details.ph-more');
  if (again && wasOpen != null) again.open = wasOpen;
}
function phDeptHeroApply() {
  const dk = _phView === 'home' && phOn() && _phKind() === 'centre' ? _phDeptHeroKey() : '';
  const hb = document.getElementById('phDh'), dt = document.getElementById('phDt');
  if (dk && hb && dt) {
    const d = DEPARTMENTS.find(x => x.key === dk);
    hb.innerHTML = _phDhHtml(d);
    _phKeepFold(dt, _phDtHtml(d));
  }
  phTilesApply();
}
function phTilesApply() {
  if (!phOn()) return;
  const one = document.getElementById('phDtOne'), dp = document.getElementById('phDtDept');
  if (one && one.dataset.dk) { const d = DEPARTMENTS.find(x => x.key === one.dataset.dk); if (d) _phKeepFold(one, _phDtHtml(d)); }
  if (dp && dp.dataset.dk && _phView === 'dept') { const d = DEPARTMENTS.find(x => x.key === dp.dataset.dk); if (d) _phKeepFold(dp, _phDtHtml(d, { head: false, first: _phSummaryTile(d) })); }
  const grid = document.getElementById('phDeptGrid');
  if (grid) grid.innerHTML = _phDeptTiles(deptHome());
}
function _phSubHead(name, sub, hue, icon) {
  return `<header class="ph-head ph-head-tint" style="--dk:var(--dept-${hue}-ink);--dt:var(--dept-${hue}-tint);--df:var(--dept-${hue}-f);--df2:var(--dept-${hue}-f2)"><span class="ph-badge ph-badge-sm">${_deptSvg(icon, 24)}</span>`
    + `<div class="ph-head-t"><h1>${esc(name)}</h1><p>${esc(sub)}</p></div></header>`;
}
// the branch login: its own name and date, what to do today, five big tiles
const _PH_BRN_HINT = { issue: 'ทุกวัน', order: 'ก่อน 13:15 สองวันก่อนส่ง', recv: 'เมื่อของมาถึง', get var() { return stkWord('เทียบสูตร', 'เทียบปกติของสาขา'); }, count: 'วันที่ 1 ของเดือน' };
function _phBranchHome() {
  const row = deptVisible().find(r => r.dept.key === 'brn');
  const tiles = row ? row.pages : [];
  // RV-M: the branch home had no way to open ค้นหา (the centre's hero has the magnifier; the phone top bar is hidden here)
  return _phSubHead(pinnedBranchName() || 'สาขาของฉัน', lnchDateLabel(), 'brn', 'store')
      .replace('</header>', `<button type="button" class="ph-hsearch" id="phBrnSearch" onclick="phOpenSearch()" aria-label="ค้นหาหน้า">${_phPath(_PH_ICON.search, 22)}</button></header>`)
    + `<section class="ph-body"><div class="ph-card ph-todo-card" aria-labelledby="phTodoH"><h2 class="ph-card-h" id="phTodoH">ต้องทำวันนี้</h2><div id="phTodoRows"><div class="ph-note">กำลังโหลด…</div></div></div>`
    + `<div class="ph-bigtiles">${tiles.map((x, n) => `<a href="#" class="ph-bigtile${n === tiles.length - 1 && tiles.length % 2 ? ' ph-span2' : ''}" data-nb="${_navPageSpec(x.p)}" onclick="phOpenDeptPage('brn',${x.i});return false">`
      + `<span class="ph-badge ph-badge-lg" style="--dk:var(--dept-brn-ink);--dt:var(--dept-brn-tint)">${_deptSvg(x.p.icon, 30)}</span><span class="ph-bigtile-n">${esc(x.p.label)}</span>`
      + `<span class="ph-bigtile-h">${esc(_PH_BRN_HINT[x.p.pane] || '')}</span></a>`).join('')}</div></section>`;
}

// ── เมื่อวาน in the header: the Daily Brief's own figure (/api/home/brief, as msLoadYesterday reads it) ─────
async function phLoadYesterday() {
  const v = document.getElementById('phYestV'); if (!v) return;
  try {
    if (!_msBriefCache['']) _msBriefCache[''] = api(`${API}/api/home/brief`);
    const brief = await _msBriefCache[''];
    if (!document.getElementById('phYestV')) return;                    // another view replaced the home meanwhile
    if (!brief || !brief.yesterday) { document.getElementById('phYest').hidden = true; return; }
    const rev = brief.revenue_yesterday || 0, vsN = brief.revenue_vs_normal_pct;
    document.getElementById('phYestL').textContent = brief.skipped_day ? 'ยอดขายวันล่าสุดที่ข้อมูลครบ' : 'ยอดขายเมื่อวาน';
    v.textContent = '฿' + fmtN(rev);
    // PR-PD (owner 2026-10-29, no. 16): the change is a pill with its arrow and word, never a colour alone
    // 2026-10-31: against "ปกติ" (the median of the same weekday over 8 weeks, rules.daily_baseline) - the one daily base
    document.getElementById('phYestS').innerHTML = vsN == null
      ? `<span class="ph-pill">${normalLbl(brief.normal_weeks, true)}</span>`
      : `${_phPill(vsN)} <span>${normalLbl(brief.normal_weeks, true)}</span>`;
    phLoadYesterdayLines();
  } catch (e) {
    delete _msBriefCache[''];
    const b = document.getElementById('phYest'); if (b) b.hidden = true;
  }
}

// PR-PD (owner 2026-10-29, phone decisions) · the three lines under the hero figure, from the same branch scorecard the Daily Brief reads:
//   no. 1  which branch to call and about what: "17 สาขาต่ำกว่าปกติ · ปลวกแดง −40% ลูกค้า −35%" (the cause beside the branch)
//   no. 4  whether the day's data is complete: "ข้อมูลครบ 27/28 สาขา" - a branch whose bills never arrived is not a branch to call
//   no. 3  what waits on the owner: the unread branch messages (nav badges, `fmsg`)
function _phPill(pct, goodUp = true) {
  if (pct == null) return '';
  const up = pct > 0, flat = pct === 0, good = flat ? null : (up === goodUp);
  return `<span class="ph-pill ${flat ? '' : good ? 'ph-pill-good' : 'ph-pill-bad'}">${flat ? '±' : up ? '▲' : '▼'} ${Math.abs(pct).toFixed(Math.abs(pct) >= 10 ? 0 : 1)}%</span>`;
}
// the cause behind a branch below its normal (scorecard: cust_vs_normal_pct, rph_vs_normal_pct, discount_pct against discount_pct_normal -
// all on the one daily baseline): the parts that moved, largest first
function _phCauseTxt(b) {
  const parts = [];
  if (b.cust_vs_normal_pct != null && b.cust_vs_normal_pct <= -8) parts.push({ k: Math.abs(b.cust_vs_normal_pct), t: `ลูกค้า −${Math.abs(b.cust_vs_normal_pct).toFixed(0)}%` });
  if (b.rph_vs_normal_pct != null && b.rph_vs_normal_pct <= -5) parts.push({ k: Math.abs(b.rph_vs_normal_pct), t: `ยอด/หัว −${Math.abs(b.rph_vs_normal_pct).toFixed(0)}%` });
  const dp = b.discount_pct, da = b.discount_pct_normal;
  if (dp != null && dp >= 2.5 && (da == null || dp >= da + 1)) parts.push({ k: dp * 4, t: `ส่วนลด ${dp.toFixed(1)}%` });
  const t = parts.sort((a, c) => c.k - a.k).slice(0, 2).map(x => x.t).join(', ');
  return t ? `(${t})` : '';
}
function _phDownRows(sc) { return belowNormalRows(sc); }      // the scorecard's one verdict (below_normal), lowest first
async function phLoadYesterdayLines() {
  const c = document.getElementById('phYestCause'), t = document.getElementById('phYestTrust'), pd = document.getElementById('phYestPend');
  if (!c || !t) return;
  try {
    if (!_msScorecard) _msScorecard = api(`${API}/api/home/branch-scorecard`);
    const sc = await _msScorecard;
    if (!document.getElementById('phYestCause')) return;
    const rows = (sc && sc.branches) || [], down = _phDownRows(sc);
    // phone B (owner 2026-10-30): the cause line as chips - the count first (red), then the two branches with what moved
    const top = down.slice(0, 2).map(b => { const why = _phCauseTxt(b).replace(/^\(|\)$/g, ''); return `<span class="ph-cz">${esc(b.branch)}${b.vs_normal_pct != null ? ' −' + Math.abs(b.vs_normal_pct).toFixed(0) + '%' : ''}${why ? ' · ' + esc(why) : ''}</span>`; });
    c.innerHTML = down.length ? `<span class="ph-cz ph-cz-bad">${fmtN(down.length)} สาขาต่ำกว่าปกติ</span>${top.join('')}` : '<span class="ph-cz ph-cz-good">ทุกสาขาอยู่ในเกณฑ์ปกติ</span>';
    c.hidden = !rows.length;
    const miss = sc.missing || rows.filter(b => b.reported === false).map(b => b.branch);
    const rep = sc.reported != null ? sc.reported : rows.length - miss.length;
    t.innerHTML = miss.length ? `<span class="ph-ink-watch">ข้อมูลยังไม่มา ${fmtN(miss.length)} สาขา</span> · ครบ ${fmtN(rep)}/${fmtN(rows.length)} <small>${esc(miss.slice(0, 3).join(' · '))}${miss.length > 3 ? ` +${miss.length - 3}` : ''}</small>`
      : `ข้อมูลครบ ${fmtN(rep)}/${fmtN(rows.length)} สาขา`;
    t.hidden = !rows.length;
  } catch (e) { _msScorecard = null; }
  try {
    const n = (typeof _NB === 'object' && _NB.msgs && _NB.msgs.fmsg) || 0;
    if (pd) { pd.innerHTML = n ? `รอคุณตอบ · <b>ข้อความจากสาขา ${fmtN(n)}</b>` : ''; pd.hidden = !n; }
  } catch (e) { /* no launcher badges yet */ }
}

// ── งานของฉันวันนี้: the open items of หน้างาน that belong to this role (the same filter as หน้างาน and the desktop launcher) ──
// PR-TF: every task carries `state` (todo | review | done) for the signed-in role; the phone shows ยังไม่เสร็จ only (the default of หน้างาน), so done
// and waiting-for-review items are not drawn. An answer without `state` (an old cache) is read from the status, as shmState does in js/16.
function _phState(it) {
  if (it && (it.state === 'todo' || it.state === 'review' || it.state === 'done')) return it.state;
  const st = it && it.status;
  return st === 'done' || st === 'na' ? 'done' : (st === 'blocked' ? 'review' : 'todo');
}
function _phGroups(items, keys) {
  // PR-U1: the open tasks are the server's (mine_open, the badges' rule); a round whose cutoff is still to come is not a task of today
  const mine = items.filter(i => _phState(i) === 'todo' && _shmMine(i) && shmIsOpenTask(i, keys || null))
    .sort((a, b) => ((b.urgent ? 1 : 0) - (a.urgent ? 1 : 0)) || (a.seq - b.seq));
  const groups = [];
  mine.forEach(i => {                                                    // the same duty for several branches is ONE row naming how many
    const k = String(i.key || '').split(':')[0] + '|' + (i.title || '');
    const g = groups.find(x => x.k === k);
    if (g) { if (i.branch && !g.branches.includes(i.branch)) g.branches.push(i.branch); g.urgent = g.urgent || !!i.urgent; }
    else groups.push({ k, first: i, branches: i.branch ? [i.branch] : [], urgent: !!i.urgent });
  });
  return groups;
}
async function phLoadTasks() {
  const seq = ++_phTodoSeq, kind = _phKind();
  const text = document.getElementById('phTaskText'), dot = document.getElementById('phTaskDot'), rows = document.getElementById('phTodoRows');
  if (kind === 'franchise') { _phTodoAt = Date.now(); return; }
  if (kind === 'centre' && !tabAllowed('supplyhome')) {   // no supply module, no task list: the Daily Brief stands in
    _phTodoAt = Date.now();
    if (text) text.textContent = tabAllowed('home') ? 'สิ่งที่ต้องดูวันนี้' : 'งานของฉันวันนี้';
    return;
  }
  let todo = null;
  try { todo = await api(`${API}/api/bom-center/todo`); } catch (e) { todo = null; }
  if (seq !== _phTodoSeq) return;
  const ok = !!(todo && Array.isArray(todo.items));
  const keys = ok ? shmOpenKeys(todo) : null;
  const role = (window.AUTH && window.AUTH.enabled && window.AUTH.role) || '';
  const steps = ok && ['analyst', 'salessupport', 'accounting'].includes(role) ? ((todo.close && todo.close.steps) || []) : [];
  _phTodoGroups = ok ? _phGroups(todo.items.concat(steps), keys) : [];
  if (ok) _phTodoAt = Date.now();
  // the count is the number of tasks (the badges' figure); a duty of several branches is one row naming how many
  const n = keys ? keys.size : _phTodoGroups.length, urgent = _phTodoGroups.some(g => g.urgent);
  // the next round of a branch (cutoff still to come): one muted line, no dot, no badge
  const next = ok && kind === 'branch' ? todo.items.filter(i => i.upcoming && i.next_th && _shmMine(i))
    .sort((a, b) => String(a.cutoff_at).localeCompare(String(b.cutoff_at))).slice(0, 1) : [];
  const nextHtml = next.map(i => `<div class="ph-todo ph-todo-next"><span class="ph-todo-main"><span class="ph-todo-d">${esc(i.next_th)}</span></span></div>`).join('');
  if (text) {
    text.textContent = !ok ? 'งานของฉันวันนี้' : n ? `งานของฉันวันนี้ ${n} งาน` : 'ไม่มีงานค้างวันนี้';
    if (dot) { dot.hidden = !ok || !n; dot.classList.toggle('is-urgent', urgent); }
  }
  if (rows) {
    rows.innerHTML = !ok ? '<div class="ph-note">โหลดรายการงานไม่ได้ ลองเปิดหน้าใหม่อีกครั้ง</div>'
      : !n ? '<div class="ph-note">ไม่มีงานค้างวันนี้</div>' + nextHtml
      : _phGroupsHtml() + nextHtml;
  }
}
function _phGroupsHtml() {
  return _phTodoGroups.map((g, i) => {
        const it = g.first, go = it.go && it.go.tab && tabAllowed(it.go.tab);
        const who = g.branches.length > 1 ? ` · ${g.branches.length} สาขา` : '';
        const inner = `<span class="ph-dot${g.urgent ? ' is-urgent' : ''}" aria-hidden="true"></span><span class="ph-todo-main"><b>${esc(it.title)}${esc(who)}</b>`
          + (it.detail ? `<span class="ph-todo-d">${esc(it.detail)}</span>` : '') + `</span>${go ? '<span class="ph-chev" aria-hidden="true">›</span>' : ''}`;
        return go ? `<a href="#" class="ph-todo" onclick="phTodoGo(${i});return false">${inner}</a>` : `<div class="ph-todo">${inner}</div>`;
      }).join('');
}
function phTodoGo(i) { const g = _phTodoGroups[i]; if (g && g.first.go) phOpenPage(() => _shmOpen(g.first.go)); }
// the งานของฉัน entry: หน้างาน for a login with the supply module, else the Daily Brief
function phOpenTasks() {
  if (tabAllowed('supplyhome')) phOpenPage(() => supplyGo({ tab: 'supplyhome' }));
  else phOpenPage(() => switchTab(tabAllowed('home') ? 'home' : firstAllowedTab()));
}

// ── a department: its pages as a two-column grid ───────────────────────────────────────────────────────
function phOpenDept(key) { _phDept = key; _phSetView('dept'); phRenderDept(key); }
function phRenderDept(key) {
  const host = document.getElementById('phv-dept'); if (!host) return;
  const row = deptVisible().find(r => r.dept.key === key);
  if (!row) { phShowHome(); return; }
  const d = row.dept;
  // phone review item 6 (owner 2026-10-31): the tiles carry ONE figure or status each (_phDtHtml), work and lines crossed first; the pages
  // with nothing to show fold under "หน้าอื่น (n)". The owner's deep summary stays the first tile of ผู้บริหาร.
  host.innerHTML = `<header class="ph-head ph-head-tint" style="--dk:var(--dept-${d.hue}-ink);--dt:var(--dept-${d.hue}-tint);--df:var(--dept-${d.hue}-f);--df2:var(--dept-${d.hue}-f2)">`
    + `<button type="button" class="ph-back" onclick="phShowHome()" aria-label="กลับหน้าแรก">${_phPath(_PH_ICON.back, 24)}</button>`
    + `<span class="ph-badge ph-badge-sm">${_deptSvg(d.icon, 24)}</span><div class="ph-head-t"><h1>${esc(d.label)}</h1><p>${esc(deptDuty(d))}</p></div></header>`
    + `<section class="ph-body" id="phDtDept" data-dk="${d.key}" style="--dk:var(--dept-${d.hue}-ink)">${_phDtHtml(d, { head: false, first: _phSummaryTile(d) })}</section>`;
  navBadgesApply();
  phTileDataLoad();
  _phFocusHead('dept');
}
// the owner's deep summary is the first tile of ผู้บริหาร (any login that may read the owner's sales)
function _phSummaryTile(d) {
  return d.key === 'mgmt' && hasModule('report.management')
    ? `<a href="#" class="ph-ptile" onclick="phShowSummary();return false">${_deptSvg('gauge', 28)}<span>สรุปผู้บริหาร</span></a>` : '';
}
function phOpenDeptPage(key, idx) {
  const d = DEPARTMENTS.find(x => x.key === key), p = d && d.pages[idx];
  if (!p) return;
  deptRemember(key); _deptFocus = null;                 // the page opened from here keeps this department lit
  phOpenPage(() => deptOpenPage(p));
}

// ── ค้นหา: the page search of the desktop (pgSearchResults, js/18-launcher.js, PR-D4), typed into a field ─────────────────────────────
// Every page this login may open; a row = icon + page name + department chip (+ the red count of the page). Before anything is typed the whole
// list is shown. The owner's summary is the first row of ผู้บริหาร.
function phOpenSearch() {
  _phSetView('search');
  phSearchRender();
  const i = document.getElementById('phSearchInput'); if (i) { try { i.focus({ preventScroll: true }); } catch (e) { /* no focus on a hidden field */ } }
}
function phSearchRender() {
  const host = document.getElementById('phSearchRes'); if (!host) return;
  const mgmt = DEPARTMENTS.find(d => d.key === 'mgmt');
  const extra = hasModule('report.management') ? [{ summary: true, label: 'สรุปผู้บริหาร', icon: 'gauge', dept: mgmt }] : [];
  _phSearchList = pgSearchResults(/** @type {HTMLInputElement} */ (document.getElementById('phSearchInput')).value || '', extra, { all: true });
  host.innerHTML = _phSearchList.length
    ? `<div class="ph-card ph-card-list" role="list">${_phSearchList.map((e, i) =>
      `<a href="#" class="ph-row ph-srow" role="listitem"${pgRowNb(e)} style="${pgRowStyle(e)}" onclick="phSearchGo(${i});return false">${pgRowInner(e)}<span class="ph-chev" aria-hidden="true">›</span></a>`).join('')}</div>`
    : '<p class="ph-empty">ไม่พบหน้าหรือสาขา</p>';
  navBadgesApply();
}
function phSearchGo(i) {
  const e = _phSearchList[i]; if (!e) return;
  if (e.summary) { phShowSummary(); return; }
  if (e.branch) { const sel = /** @type {HTMLSelectElement|null} */ (document.getElementById('selBranch')); if (sel) sel.value = e.branch; _deptCur = null; _deptFocus = null; phOpenPage(() => switchTab('home')); return; }
  if (e.dept) deptRemember(e.dept.key); else _deptCur = null;
  _deptFocus = null;
  phOpenPage(() => { if (e.pane) supplyGo({ tab: e.tab, pane: e.pane }); else switchTab(e.tab); });
}

// ── ฉัน: who is signed in, the theme, the write lock, notifications, the install card, password, sign out ─────
function phOpenMe() {
  _phSetView('me');
  phMeRender();
  _phFocusHead('me');
  if (tabAllowed('supplyhome') && window.writeToken && window.writeToken.gated === null) verifyWriteToken().then(phMeRender).catch(() => {});
  _msPushOpen = true; try { msPushInit(); } catch (e) { /* push is not supported here */ }
}
function phMeRender() {
  const a = window.AUTH || {}, signed = !!(a.enabled && a.authenticated);
  const who = lnchWho(), set = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
  set('phMeWho', who);
  set('phMeAv', (who || '?').trim().charAt(0).toUpperCase());
  const roleTh = String(ROLE_LABEL_TH[a.role] || a.role || '').split(' (')[0];
  set('phMeRole', signed ? [roleTh === who ? '' : roleTh, isPinnedSession() ? a.branch : '', a.username].filter(Boolean).join(' · ') : 'ไม่ได้เปิดการเข้าสู่ระบบ');
  phDarkSync();
  const lock = document.getElementById('phMeLock'), st = window.writeToken || {};
  if (lock) {
    lock.hidden = !(tabAllowed('supplyhome') && st.gated !== false && st.gated !== null);
    set('phMeLockV', st.unlocked ? 'แก้ไขได้' : 'อ่านอย่างเดียว');
    lock.classList.toggle('is-on', !!st.unlocked);
  }
  ['phMePw', 'phMeOut'].forEach(id => { const el = document.getElementById(id); if (el) el.hidden = !signed; });
  const full = document.getElementById('phMeFull'); if (full) full.hidden = !(phOn() && _phKind() === 'centre');   // phone B: the old "เต็ม →"
  // PR-MA: หน้าที่เปิดล่าสุด, the last three pages of this device, first
  const rw = document.getElementById('phMeRecentWrap'), rl = document.getElementById('phMeRecent');
  if (rw && rl) { const r = _phRecentHtml(); rl.innerHTML = r.html; rw.hidden = !r.n; try { navBadgesApply(); } catch (e) { /* no launcher slice */ } }
}
function phHelpToggle(btn) {   // the "?" of a card: its explanation is folded until asked for
  const card = btn.closest('.ms-section'); if (!card) return;
  const on = card.classList.toggle('ph-open');
  btn.setAttribute('aria-expanded', on ? 'true' : 'false');
}
function phDarkSync() {
  const on = document.body.classList.contains('dark'), b = document.getElementById('phMeDark');
  if (b) b.setAttribute('aria-pressed', on ? 'true' : 'false');
  const v = document.getElementById('phMeDarkV'); if (v) v.textContent = on ? 'เปิด' : 'ปิด';
}
function phUnlockWrites() { unlockWrites().then(phMeRender).catch(() => {}); }

// ════════════════════════════════════════════════════════════════
// PHONE B (owner 2026-10-30, mockup "แบบ B")
// ════════════════════════════════════════════════════════════════
// ONE header (#phTop) for every view of a centre login: the title (a back arrow on a detail screen or a department), the period and the
// branch chips - the summary's own #msMonthSel / #msBranch are moved inside them, laid over the chip, so a tap opens the same picker and its
// change runs msPickChanged as before - and ฉัน. A page of the full app keeps its own top bar, drawn in the same colours (index.html).
// หน้าแรก of a login with report.management = the hero (เมื่อวาน, full width) + the overview cards (phOvRender) from the ONE management
// payload (_msRpt); a card opens its DETAIL SCREEN: #mobileSummary under the same header, showing the existing view (sales / branches /
// menu / profit / discounts) or the one section of msv-summary it is about. Opening pushes a history entry (pushState) so the back arrow
// and the phone's back button both return to the cards at the scroll position they were left at (popstate). No route, no new call.
const _PH_TOP_T = { home: 'หน้าแรก', search: 'ค้นหา', me: 'ฉัน', dept: 'แผนก' };
const _PH_DETAIL = {
  yesterday: { v: 'summary', only: 'yest', t: 'เมื่อวาน' },
  month: { v: 'summary', only: 'kpis', t: 'เดือนนี้' },
  target: { v: 'summary', only: 'target', t: 'เป้าเดือนนี้' },
  todo: { v: 'summary', only: 'actions', t: 'ต้องทำ' },
  watch: { v: 'summary', only: 'watch', t: 'ตรวจวันนี้' },
  voice: { v: 'summary', only: 'voice', t: 'เสียงลูกค้า' },
  sales: { v: 'sales', t: 'ยอดขาย' },
  branches: { v: 'branches', t: 'สาขา' },
  menu: { v: 'menu', t: 'เมนู' },
  profit: { v: 'profit', t: 'ต้นทุนอาหารและกำไร' },
  discounts: { v: 'discounts', t: 'ส่วนลด' },
  voids: { v: 'voids', t: 'บิลยกเลิก / พิมพ์ซ้ำ' },   // 2026-10-31 (owner): the ตรวจวันนี้ card's drill-down (msLoadVoids)
};
let _phDetail = '', _phDetailPushed = false, _phHomeY = 0, _msVoiceN = 0;
// the two pickers live in the header's chips while the shell is on (and go back to the summary when it goes)
function _phTopPickers(on) {
  [['msMonthSel', 'phTopMonth'], ['msBranch', 'phTopBranch']].forEach(([s, c]) => {
    const sel = /** @type {any} */ (document.getElementById(s)), chip = document.getElementById(c);
    if (!sel) return;
    if (on && chip && sel.parentNode !== chip) {
      if (!sel._phAnchor) { sel._phAnchor = document.createComment(s); sel.parentNode.insertBefore(sel._phAnchor, sel); }
      chip.appendChild(sel);
    } else if (!on && sel._phAnchor && sel._phAnchor.parentNode) {
      sel._phAnchor.parentNode.insertBefore(sel, sel._phAnchor); sel._phAnchor.remove(); sel._phAnchor = null;
    }
  });
}
// the chip labels: the option on show; filled (is-on) when it is not the default (an older month, one branch); no picker = no chip (a pinned login)
function phTopSync() {
  [['msMonthSel', 'phTopMonthL'], ['msBranch', 'phTopBranchL']].forEach(([s, l]) => {
    const sel = /** @type {HTMLSelectElement|null} */ (document.getElementById(s)), lab = document.getElementById(l), chip = lab && lab.parentElement;
    if (!lab || !chip) return;
    chip.hidden = !sel || sel.tagName !== 'SELECT';
    if (chip.hidden) return;
    const o = sel.options[sel.selectedIndex];
    lab.textContent = o ? (o.textContent.replace(/\s*\(.*\)\s*$/, '') || o.textContent) : '';   // "ต.ค. 2026 (ถึงวันที่ 22)" → "ต.ค. 2026" on the chip
    chip.classList.toggle('is-on', sel.selectedIndex > 0);
  });
}
function phTopRender() {
  const top = document.getElementById('phTop'); if (!top) return;
  const on = phOn() && _phKind() === 'centre', ms = on && typeof hasModule === 'function' && hasModule('report.management');
  document.body.classList.toggle('ph-centre', on);
  _phTopPickers(ms);
  if (!on) return;
  const det = _phView === 'summary', dept = _phView === 'dept', back = det || dept;
  const D = det ? _PH_DETAIL[_phDetail] : null;
  const row = dept && typeof DEPARTMENTS !== 'undefined' ? DEPARTMENTS.find(x => x.key === _phDept) : null;
  const bk = document.getElementById('phTopBack'), mk = document.getElementById('phTopMark');
  if (bk) { bk.hidden = !back; bk.setAttribute('aria-label', dept ? 'กลับหน้าแรก' : 'กลับภาพรวม'); }
  if (mk) mk.hidden = back;
  const t = document.getElementById('phTopT');
  if (t) t.textContent = det ? (_phDetail === 'voids' && _msVoidBr ? _msVoidBr : D ? D.t : 'สรุปผู้บริหาร') : row ? row.label : (_PH_TOP_T[_phView] || 'หน้าแรก');
  const chips = document.getElementById('phTopChips'); if (chips) chips.hidden = !(ms && (det || _phView === 'home'));
  const av = document.getElementById('phTopAv'); if (av) { av.textContent = _phInitial(); av.classList.toggle('is-on', _phView === 'me'); }
  phTopSync();
}
// ── a detail screen ──
function phOpenDetail(d) {
  if (!_PH_DETAIL[d]) d = d === 'summary' ? 'yesterday' : 'month';
  if (d === 'profit' && !(_msMod('cost.analysis') || _msMod('profit.pnl'))) d = 'month';
  if (d === 'discounts' && !_msMod('ops.discounts')) d = 'branches';
  if (_phView === 'home') _phHomeY = window.scrollY;
  const st = { phd: d };
  try {
    if (_phDetailPushed && history.state && history.state.phd) history.replaceState(st, '');
    else { history.pushState(st, ''); _phDetailPushed = true; }
  } catch (e) { /* no history (a sandboxed frame): the back arrow still closes */ }
  _phShowDetail(d);
}
function _phShowDetail(d) {
  const D = _PH_DETAIL[d] || _PH_DETAIL.month;
  _phDetail = d; _msView = D.v;
  const sv = document.getElementById('msv-summary');
  if (sv) { if (D.only) sv.dataset.only = D.only; else delete sv.dataset.only; }
  document.querySelectorAll('#msv-summary > .ms-section').forEach(s => s.classList.toggle('ph-off', !!D.only && /** @type {HTMLElement} */ (s).dataset.d !== D.only));
  _phSetView('summary');
  msShowView(D.v);
  if (!window.__dataLastDate) { try { initDataCoverageNote(); } catch (e) { /* the note is non-critical */ } }
  if (_msRpt) msRenderActive(); else loadMobileSummary(true);
  _phFocusHead('summary');
}
// the back arrow: a department → หน้าแรก; a detail screen → its history entry goes (popstate shows the cards), or the cards straight away
function phDetailClose() {
  if (_phView === 'dept') { phShowHome(); return; }
  if (_phView !== 'summary') return;
  if (_phDetailPushed && history.state && history.state.phd) { history.back(); return; }
  _phBackToCards();
}
function _phBackToCards() {
  _phDetailPushed = false; _phDetail = '';
  const y = _phHomeY, host = document.getElementById('phv-home');
  _phSetView('home');
  if (!host || !host.querySelector('#phOvGrid')) phRenderHome(); else phOvRender();
  requestAnimationFrame(() => window.scrollTo(0, y));
}
window.addEventListener('popstate', e => {
  if (!phOn() || _phKind() !== 'centre') return;
  const d = e.state && e.state.phd;
  if (d) { if (d === 'voids') _msVoidFromState(e.state); if (_phView === 'home' || _phView === 'summary') { if (_phView === 'home') _phHomeY = window.scrollY; _phDetailPushed = true; _phShowDetail(d); } return; }   // forward again
  if (_phView === 'summary') _phBackToCards(); else _phDetailPushed = false;
});
// ── the overview cards of หน้าแรก ──
function phOvLoad() {
  if (!document.getElementById('phOvGrid')) return;
  phOvRender();
  loadMobileSummary(true);                         // the payload is cached per (month, branch): a second visit draws from memory
}
function phOvKey(e, d) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); phOpenDetail(d); } }
function phOvRender() {
  const box = document.getElementById('phOvGrid'); if (!box) return;
  const j = _msRpt, st = document.getElementById('phOvSt');
  if (!j) { box.innerHTML = `<p class="ph-note">${esc((document.getElementById('msStatus') || {}).textContent || 'กำลังโหลด…')}</p>`; return; }
  if (st) st.textContent = [msBranch() || 'ทุกสาขา', j.label || ''].filter(Boolean).join(' · ');
  const H = j.headline || {}, P = j.pace || {}, cards = [];
  const card = (d, tone, label, val, sub, o) => cards.push(`<div class="ph-ov-card${tone ? ' is-' + tone : ''}${o && o.wide ? ' ph-ov-wide' : ''}" role="button" tabindex="0" data-d="${d}" onclick="phOpenDetail('${d}')" onkeydown="phOvKey(event,'${d}')">`
    + `<span class="ph-ov-l">${label}</span><span class="ph-ov-v${o && o.ink ? ' is-' + o.ink : ''}">${val}</span>${sub ? `<span class="ph-ov-s">${sub}</span>` : ''}${o && o.rows ? `<div class="ph-ov-rows">${o.rows}</div>` : ''}</div>`);
  const safe = (k, fn) => { try { fn(); } catch (e) { console.error('overview card ' + k, e); } };   // one card that fails never blanks the others
  safe('month', () => card('month', '', j.partial ? 'เดือนนี้ (สะสม)' : esc(j.label || 'ยอดขายเดือน'), '฿' + fmt(H.revenue || 0),
    H.rev_wow_pct != null ? `${msPchip(H.rev_wow_pct)}<span>เทียบเดือนก่อน</span>` : ''));
  safe('target', () => {
    if (!P.target) return;                         // no target set = no card (owner rule: nothing to show, not shown)
    const pvt = P.proj_vs_target_pct, RR = P.run_rate, miss = pvt != null && pvt < 100, tone = pvt == null ? '' : miss ? 'bad' : 'good';
    const sub = RR && RR.days_left > 0 && RR.need_per_day != null
      ? (RR.need_per_day <= 0 ? '<b class="is-good">ถึงเป้าแล้ว</b>' : `<span>ต้องขายวันละ</span><b class="${RR.need_per_day > (RR.normal_per_day || 0) * 1.02 ? 'is-bad' : 'is-good'}">฿${fmt(RR.need_per_day)}</b>`)
      : `<span>${P.month_complete ? 'ของเป้าทั้งเดือน' : 'ประมาณการสิ้นเดือน'}</span>`;
    card('target', tone, 'เป้า', pvt != null ? pvt.toFixed(0) + '%' : '—', sub, { ink: tone });
  });
  safe('todo', () => {
    const A = j.actions || []; if (!A.length) return;
    const neg = A.reduce((s, a) => s + (a.baht != null && a.baht < 0 ? a.baht : 0), 0);
    card('todo', 'bad', 'ต้องทำ', `${fmtN(A.length)} เรื่อง`, neg ? `<b class="is-bad">${dvBaht(neg)}</b>` : '');
  });
  safe('watch', () => {
    const W = j.watch, n = W ? (W.fix || 0) + (W.watch || 0) : 0; if (!n) return;
    const live = (W.items || []).filter(it => it.available && (it.level === 'fix' || it.level === 'watch'));
    card('watch', 'watch', 'ตรวจวันนี้', `${fmtN(n)} เรื่อง`, `<span>${esc(live.slice(0, 2).map(it => it.title).join(' · '))}</span>`);
  });
  safe('profit', () => {
    const fc = P.food_cost_pct; if (fc == null || !canSeeCost() || !(_msMod('cost.analysis') || _msMod('profit.pnl'))) return;
    const fct = fcTargetSet(j.food_cost_target), over = fc > fct;
    card('profit', over ? 'bad' : 'good', 'ต้นทุนอาหาร', fc.toFixed(1) + '%', `<b class="${over ? 'is-bad' : 'is-good'}">${over ? 'เกินเป้า' : 'ในเป้า'}</b><span>เป้า ≤${fcTargetTxt(fct)}%</span>`, { ink: over ? 'bad' : 'good' });
  });
  safe('discounts', () => {
    if (!_msMod('ops.discounts') || H.discount_pct == null) return;
    const pol = H.discount_policy_pct, over = pol != null && H.discount_pct > pol;
    card('discounts', over ? 'bad' : pol != null ? 'good' : '', 'ส่วนลด', H.discount_pct.toFixed(1) + '%',
      `<span>${pol != null ? `เพดาน ${pol}%` : 'ของยอดขาย'}</span>${over ? '<b class="is-bad">เกินเพดาน</b>' : ''}`, { ink: over ? 'bad' : '' });
  });
  safe('branches', () => {
    const brs = (j.branches || []).filter(b => b.rev_delta != null); if (brs.length < 2) return;
    const dn = brs.filter(b => b.rev_delta < 0).length, up = brs.filter(b => b.rev_delta > 0).length;
    const rows = brs.slice().sort((a, b) => a.rev_delta - b.rev_delta).slice(0, 2).map(b => msRow2({ tone: b.rev_delta < 0 ? 'bad' : 'good', title: esc(b.branch),
      val: dvBaht(b.rev_delta), valTone: b.rev_delta < 0 ? 'bad' : 'good', fact: `${b.wow_pct != null ? msPchip(b.wow_pct) + ' ' : ''}ยอด ฿${fmt(b.revenue || 0)}`, branch: b.branch })).join('');
    card('branches', dn ? 'bad' : 'good', 'สาขา', `ลด ${fmtN(dn)} · เพิ่ม ${fmtN(up)}`, '<span>เทียบเดือนก่อน เป็นบาท</span>', { wide: true, rows });
  });
  safe('menu', () => {
    const M = j.menu || {}, cov = M.covers || 0, rs = (M.risers || []).filter(m => (m.delta || 0) > 0);
    if (canSeeCost() && !M.hidden && rs.length && cov) {
      card('menu', 'bad', 'เมนู', '+฿' + fmt(rs.reduce((s, m) => s + m.delta * cov, 0)), `<span>${fmtN(rs.length)} เมนูดันต้นทุนต่อหัว</span>`, { ink: 'bad' });
    } else card('menu', '', 'เมนู', 'ขายดี', '<span>จำนวนจานรายเมนู</span>');
  });
  safe('sales', () => {
    const pk = (j.peak || {}).peak_hour;
    card('sales', '', 'ยอดขาย', `${fmtN(H.customers || 0)} คน`, [H.rev_per_head != null ? `฿${fmt(H.rev_per_head)}/หัว` : '', pk ? `พีค ${_hh(pk.hour)}` : ''].filter(Boolean).map(x => `<span>${x}</span>`).join(''));
  });
  if (_msVoiceN) card('voice', 'watch', 'เสียงลูกค้า', `${fmtN(_msVoiceN)}${_msVoiceN >= 5 ? '+' : ''} รอตอบ`, '');
  box.innerHTML = cards.join('') || '<p class="ph-note">ยังไม่มีข้อมูลของเดือนนี้</p>';
}

// ── Pull to refresh on the summary: a downward drag from the top grows #msPull
// ("ดึงลงเพื่อรีเฟรช" → "ปล่อยเพื่อรีเฟรช"), letting go past the threshold runs msRefresh().
// body:not(.force-desktop) has overscroll-behavior-y: contain (index.html) so the
// browser's own page reload does not fire first. Off while the branch sheet is open. ──
(function () {
  const root = document.getElementById('mobileSummary'), ind = document.getElementById('msPull');
  if (!root || !ind) return;
  const ARM = 140, MAXH = 64;
  let y0 = null, dist = 0, pulling = false;
  root.addEventListener('touchstart', e => {
    if (document.body.classList.contains('ms-sheet-open') || window.scrollY > 0) { y0 = null; return; }
    y0 = e.touches[0].clientY; dist = 0; pulling = false;
  }, { passive: true });
  root.addEventListener('touchmove', e => {
    if (y0 == null) return;
    dist = e.touches[0].clientY - y0;
    if (dist <= 0 || window.scrollY > 0) { if (pulling) { pulling = false; ind.style.height = '0px'; } return; }
    pulling = true;
    ind.style.height = Math.min(MAXH, dist * 0.45) + 'px';
    ind.classList.toggle('ready', dist >= ARM);
    ind.textContent = dist >= ARM ? '↑ ปล่อยเพื่อรีเฟรช' : '↓ ดึงลงเพื่อรีเฟรช';
  }, { passive: true });
  root.addEventListener('touchend', () => {
    if (y0 != null && pulling && dist >= ARM) {
      ind.textContent = 'กำลังรีเฟรช…'; ind.style.height = '36px';
      try { msRefresh(); } catch (e) {}
      setTimeout(() => { ind.style.height = '0px'; }, 900);
    } else ind.style.height = '0px';
    y0 = null; dist = 0; pulling = false; ind.classList.remove('ready');
  }, { passive: true });
})();

// ════════════════════════════════════════════════════════════════
// THE PERIOD SHEET (owner 2026-10-25: "ทำให้เลือกช่วงเวลาได้ด้วยนะครับ")
// ════════════════════════════════════════════════════════════════
// On a phone the top bar's period control sits in the folded tools. The range chip by the page title (#gpRangeChip, a one-line
// button here: "1–24 ต.ค. 69 ▾") opens a bottom sheet that HOLDS the top bar's own control: #gPeriod is moved into the sheet while it
// is open and put back where it was when it closes - the same buttons, the same state (_gp), the same gPeriod* handlers, so a page
// still has ONE period control (backend/test_period_one_control.py). Every page that follows the period (TABS[tab].follow.period) has
// the chip, so this works on all of them. Closes on ✕ / เสร็จ, the backdrop, Esc, a swipe down, leaving the page or the phone width.
let _phpHome = null, _phpBack = null;
function _phpEnsure() {
  let sh = document.getElementById('phPeriodSheet');
  if (sh) return sh;
  sh = document.createElement('div');
  sh.id = 'phPeriodSheet'; sh.className = 'php-back'; sh.hidden = true;
  sh.innerHTML = `<div class="php-box" role="dialog" aria-modal="true" aria-labelledby="phpTitle">
      <div class="php-grip" aria-hidden="true"></div>
      <div class="php-head"><div class="php-ht"><p id="phpTitle" class="php-title">ช่วงเวลา</p><p id="phpRange" class="php-range"></p></div>
        <button type="button" class="php-x" onclick="phPeriodClose()" aria-label="ปิด">✕</button></div>
      <div id="phpSlot" class="php-slot"></div>
      <button type="button" class="php-done" onclick="phPeriodClose()">เสร็จ</button>
    </div>`;
  sh.addEventListener('click', e => { if (e.target === sh) phPeriodClose(); });
  sh.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); phPeriodClose(); } });
  let y0 = null;
  sh.addEventListener('touchstart', e => { y0 = e.target.closest && e.target.closest('input, select') ? null : e.touches[0].clientY; }, { passive: true });
  sh.addEventListener('touchend', e => { if (y0 != null && e.changedTouches[0].clientY - y0 > 80) phPeriodClose(); y0 = null; }, { passive: true });
  document.body.appendChild(sh);
  return sh;
}
function phPeriodOpen() {
  const chip = document.getElementById('gpRangeChip'), gp = document.getElementById('gPeriod');
  if (!phOn() || !gp || !chip || chip.hidden) return;
  phBranchClose();
  const sh = _phpEnsure(), slot = document.getElementById('phpSlot');
  if (!slot) return;
  if (!_phpHome) { _phpHome = document.createComment('gPeriod'); gp.parentNode.insertBefore(_phpHome, gp); }
  slot.appendChild(gp);
  const rg = document.getElementById('phpRange'); if (rg) rg.textContent = chip.title || '';
  _phpBack = document.activeElement;
  sh.hidden = false; document.body.classList.add('php-open');
  chip.setAttribute('aria-expanded', 'true');
  const on = gp.querySelector('.gp-kinds .gran-active') || gp.querySelector('button:not([hidden])');
  try { if (on) /** @type {HTMLElement} */ (on).focus({ preventScroll: true }); } catch (e) { /* no focus */ }
}
function phPeriodClose() {
  const sh = document.getElementById('phPeriodSheet'), gp = document.getElementById('gPeriod');
  if (_phpHome && gp) { _phpHome.parentNode.insertBefore(gp, _phpHome); _phpHome.remove(); _phpHome = null; }
  if (!sh || sh.hidden) return;
  sh.hidden = true; document.body.classList.remove('php-open');
  const chip = document.getElementById('gpRangeChip'); if (chip) chip.setAttribute('aria-expanded', 'false');
  try { if (_phpBack && _phpBack.isConnected) _phpBack.focus({ preventScroll: true }); } catch (e) { /* no focus */ }
  _phpBack = null;
}
function phPeriodChipKey(e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); phPeriodOpen(); } }

// ════════════════════════════════════════════════════════════════
// THE BRANCH SHEET (owner 2026-10-25: "หน้านี้อยากให้ filter รายสาขาได้ด้วย")
// ════════════════════════════════════════════════════════════════
// The branch chip by the page title (#gpBranchChip, "ทุกสาขา ▾", branchChipRender in js/01-core.js) opens a bottom sheet over the top
// bar's own branch filter: a search box and the options of #selBranch (by group, สาขาหลัก / แฟรนไชส์, when both are there). Picking one
// sets #selBranch and fires its change event - the same onFilterChange() the top bar runs, so the page reloads with branch= exactly as
// from the top bar. The sheet keeps no branch of its own (the list is drawn from #selBranch each time). A pinned login (PR-K) has no
// chip and no sheet (stripBranchPickers). Closes on ✕, the backdrop, Esc, a swipe down, a pick, leaving the page or the phone width.
let _phbBack = null;
function _phbEnsure() {
  let sh = document.getElementById('phBranchSheet');
  if (sh) return sh;
  sh = document.createElement('div');
  sh.id = 'phBranchSheet'; sh.className = 'php-back'; sh.hidden = true;
  sh.innerHTML = `<div class="php-box" role="dialog" aria-modal="true" aria-labelledby="phbTitle">
      <div class="php-grip" aria-hidden="true"></div>
      <div class="php-head"><div class="php-ht"><p id="phbTitle" class="php-title">สาขา</p><p id="phbCur" class="php-range"></p></div>
        <button type="button" class="php-x" onclick="phBranchClose()" aria-label="ปิด">✕</button></div>
      <label for="phbSearch" class="sr-only">ค้นหาสาขา</label>
      <input id="phbSearch" class="phb-search" type="search" placeholder="ค้นหาสาขา" autocomplete="off" oninput="_phbDraw()">
      <div id="phbList" class="phb-list" role="radiogroup" aria-labelledby="phbTitle"></div>
    </div>`;
  sh.addEventListener('click', e => {
    if (e.target === sh) { phBranchClose(); return; }
    const b = e.target.closest && e.target.closest('.phb-item');
    if (b) phBranchPick(b.getAttribute('data-v') || '');
  });
  sh.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); phBranchClose(); } });
  let y0 = null;
  sh.addEventListener('touchstart', e => { y0 = e.target.closest && e.target.closest('input, .phb-list') ? null : e.touches[0].clientY; }, { passive: true });
  sh.addEventListener('touchend', e => { if (y0 != null && e.changedTouches[0].clientY - y0 > 80) phBranchClose(); y0 = null; }, { passive: true });
  document.body.appendChild(sh);
  return sh;
}
// the list: ทุกสาขา first, then the open branches (by group when the list holds both), filtered by the search text; the closed
// branches (owner 2026-10-26 "Hide closed branches": #selBranch's "สาขาที่ปิดแล้ว" options, data-closed) sit last in a fold of their
// own, open when one of them is the branch on screen or the search finds one, so a closed branch's history stays reachable
function _phbDraw() {
  const sel = /** @type {HTMLSelectElement|null} */ (document.getElementById('selBranch')), list = document.getElementById('phbList');
  if (!sel || sel.tagName !== 'SELECT' || !list) return;
  const inp = /** @type {HTMLInputElement|null} */ (document.getElementById('phbSearch'));
  const norm = s => (typeof pgNorm === 'function' ? pgNorm(s) : String(s || '').toLowerCase().replace(/\s+/g, ''));
  const q = norm(inp ? inp.value : ''), cur = sel.value;
  const opts = [...sel.options], all = opts.find(o => !o.value);
  const hits = opts.filter(o => o.value && (!q || norm(o.value).includes(q)));
  const rows = hits.filter(o => !o.dataset.closed), shut = hits.filter(o => o.dataset.closed);
  const item = (v, label) => `<button type="button" class="phb-item" role="radio" aria-checked="${v === cur}" data-v="${esc(v)}">${esc(label)}</button>`;
  const types = new Set(opts.filter(o => o.value && !o.dataset.closed).map(o => o.dataset.type || 'master'));
  let html = !q && all ? item('', all.textContent || 'ทุกสาขา') : '';
  if (types.size > 1) {
    [['master', 'สาขาหลัก'], ['franchise', 'แฟรนไชส์']].forEach(([t, h]) => {
      const g = rows.filter(o => (o.dataset.type || 'master') === t);
      if (g.length) html += `<p class="phb-gh">${h}</p>` + g.map(o => item(o.value, o.textContent || o.value)).join('');
    });
  } else html += rows.map(o => item(o.value, o.textContent || o.value)).join('');
  if (shut.length) {
    const open = !!q || shut.some(o => o.value === cur);
    html += `<details class="phb-closed"${open ? ' open' : ''}><summary class="phb-gh">สาขาที่ปิดแล้ว (${shut.length})</summary>`
      + shut.map(o => item(o.value, o.textContent || o.value)).join('') + '</details>';
  }
  list.innerHTML = html || '<p class="phb-empty">ไม่พบสาขาที่ค้นหา</p>';
}
function phBranchOpen() {
  const chip = document.getElementById('gpBranchChip'), sel = document.getElementById('selBranch');
  if (!phOn() || isPinnedSession() || !chip || chip.hidden || !sel || sel.tagName !== 'SELECT') return;
  phPeriodClose();
  const sh = _phbEnsure();
  const inp = /** @type {HTMLInputElement|null} */ (document.getElementById('phbSearch')); if (inp) inp.value = '';
  const cur = document.getElementById('phbCur'); if (cur) cur.textContent = (chip.title || '').replace(/^สาขา: /, 'ตอนนี้: ');
  _phbDraw();
  _phbBack = document.activeElement;
  sh.hidden = false; document.body.classList.add('php-open');
  chip.setAttribute('aria-expanded', 'true');
  const on = sh.querySelector('.phb-item[aria-checked="true"]') || sh.querySelector('.phb-item');
  try { if (on) /** @type {HTMLElement} */ (on).focus({ preventScroll: true }); } catch (e) { /* no focus */ }
}
function phBranchClose() {
  const sh = document.getElementById('phBranchSheet');
  if (!sh || sh.hidden) return;
  sh.hidden = true; document.body.classList.remove('php-open');
  const chip = document.getElementById('gpBranchChip'); if (chip) chip.setAttribute('aria-expanded', 'false');
  try { if (_phbBack && _phbBack.isConnected) _phbBack.focus({ preventScroll: true }); } catch (e) { /* no focus */ }
  _phbBack = null;
}
// a pick: the top bar's own select takes it and runs its own change handler (onFilterChange), as a tap there would
function phBranchPick(v) {
  const sel = /** @type {HTMLSelectElement|null} */ (document.getElementById('selBranch'));
  phBranchClose();
  if (!sel || sel.tagName !== 'SELECT' || ![...sel.options].some(o => o.value === v) || sel.value === v) return;
  sel.value = v;
  sel.dispatchEvent(new Event('change', { bubbles: true }));
}

// ════════════════════════════════════════════════════════════════
// PHONE DISPLAY MODES (PR-MA, 2026-10-24)
// ════════════════════════════════════════════════════════════════
// Every page of the phone shell opens in one of two modes (the default of each page: PAGE_PHONE in js/01-core.js):
//   แตะง่าย (touch) - the phone layout: 12 px text floor, 44 px targets, stacked cards (the stylesheet's <= 700 px rules);
//   พอดีจอ (fit)    - the page's desktop layout in a frame _PH_FIT_MIN-_PH_FIT_MAX px wide (the page's own content width, 820 px when it cannot be
//                    read), scaled to the screen width with CSS zoom (transform: scale where zoom is not supported). The frame is never wider than
//                    the width at which 12 px text still reads >= 5.5 px (_PH_FIT_TEXT / _PH_FIT_FLOOR), and the text inside it is at least 12 px,
//                    so the smallest text is >= 5.5 px at rest and >= 12 px when a table or a chart is shown at 100 % (a double tap).
// The frame is the open page's panel (#tab-<name>.ph-frame); the top bar, the chip rows and the bottom bar sit outside it. Inside it the filters,
// the period pickers, the sub-tab chips and the "?" are counter-zoomed back to touch size (.ph-tz). The browser's own pinch zoom stays.
// A tap on a table row opens the row sheet; a double tap on a table or a chart shows it at 100 % (again: back). The choice is remembered per page
// on this device (localStorage, oshinei_phmode).
const _PH_FIT_MIN = 760, _PH_FIT_MAX = 900, _PH_FIT_DEF = 820, _PH_FIT_TEXT = 12, _PH_FIT_FLOOR = 5.5, _PH_MODE_KEY = 'oshinei_phmode';
// .ph-tz-unit (PR-TD): a card's own control row counter-zoomed as ONE unit, so its period track, its select and its buttons keep one height
// .section-title (2026-10-26): a card's heading is counter-zoomed with its "?" as one unit, so it reads at 15 px with the "?" right after it
const _PH_TZ_SEL = '.ph-tz-unit, .section-title, .ui-q, select, input[type="month"], input[type="date"], input[type="search"], input[type="text"], input:not([type]), .subtab-btn, .gran-btn, .wt-seg button, [role="tab"], .wt-find, .bmc-pager button';
let _phFitT = null, _phFitPanel = null, _phFitObs = null, _phFitKey = '';
const _phZoomOK = (() => { try { return CSS.supports('zoom', '0.5'); } catch (e) { return false; } })();
// the page on screen: its tab, its pane (a supply tab) and, for a page with two views, which one is showing
function phCurPage() {
  if (typeof activeTab !== 'string' || !activeTab) return null;
  const acc = typeof _SUPPLY_PANE === 'object' ? _SUPPLY_PANE[activeTab] : null;
  const pane = acc ? acc.get() : '';
  return { tab: activeTab, pane: pane || '', view: _phPageView(activeTab, pane || '') };
}
function _phPageKey(pg) { return pg.tab + (pg.pane ? '|' + pg.pane : '') + (pg.view ? '#' + pg.view : ''); }
// ต้นทุนต่อจาน: the table, or the import (a file read and waiting to be saved); ทะเบียนวัตถุดิบ: the list, or editing it (a field changed or in use)
function _phPageView(tab, pane) {
  if (tab !== 'bomcenter') return '';
  try {
    if (pane === 'dishcost') return typeof _bmcDcPre !== 'undefined' && _bmcDcPre ? 'import' : 'table';
    if (pane === 'ing') {
      const ed = typeof _bmcIngEdits === 'object' && _bmcIngEdits && Object.keys(_bmcIngEdits).length;
      const ae = document.activeElement;
      return ed || (ae && ae.closest && ae.closest('#bmcIngBody') && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName)) ? 'edit' : 'list';
    }
  } catch (e) { /* the page's slice is not loaded */ }
  return '';
}
function _phModes() { try { return JSON.parse(localStorage.getItem(_PH_MODE_KEY) || '{}') || {}; } catch (e) { return {}; } }
function phModeOf(pg) {
  const own = _phModes()[_phPageKey(pg)];
  return own === 'fit' || own === 'touch' ? own : phonePageDefault(pg.tab, pg.pane, pg.view);
}
// the page may take พอดีจอ: a page of the phone shell, not the branch login (its pages are the touch layout, ตัวอักษรใหญ่ is its option)
function _phFitAllowed() { return phOn() && _phView === 'page' && !document.body.classList.contains('supply-phone'); }
function phModeToggle() {
  const pg = phCurPage(); if (!pg) return;
  const next = phModeOf(pg) === 'fit' ? 'touch' : 'fit';
  const m = _phModes(); m[_phPageKey(pg)] = next;
  try { localStorage.setItem(_PH_MODE_KEY, JSON.stringify(m)); } catch (e) { /* private mode: this visit only */ }
  phFitApply();
  try { loadActiveTab(); } catch (e) { /* the page draws again with the mode's charts and lists */ }
  try { uiToast(next === 'fit' ? 'พอดีจอ' : 'แตะง่าย'); } catch (e) { /* no toast yet */ }
}
function phFitSchedule() { clearTimeout(_phFitT); _phFitT = setTimeout(phFitApply, 0); }
function phFitApply() {
  const pg = phCurPage();
  const panel = pg ? document.getElementById('tab-' + pg.tab) : null;
  const fit = !!(panel && pg && _phFitAllowed() && phModeOf(pg) === 'fit');
  if (_phFitPanel && (_phFitPanel !== panel || !fit)) _phFitOff(_phFitPanel);
  document.body.classList.toggle('ph-fit', fit);
  _phModeBtnSync(pg, fit);
  if (pg && _phView === 'page') phRecentAdd(pg);
  phCardsSchedule();                              // PR-MB: แตะง่าย draws the page's daily tables as cards; พอดีจอ takes them away
  if (!fit) return;
  const key = _phPageKey(pg);
  if (_phFitPanel !== panel || _phFitKey !== key) { _phFitPanel = panel; _phFitKey = key; panel.classList.add('ph-frame'); panel.dataset.phw = ''; _phFitWatch(panel); }
  phFitLayout(panel);
}
function _phModeBtnSync(pg, fit) {
  const b = document.getElementById('phModeBtn'), l = document.getElementById('phModeL');
  if (!b) return;
  b.hidden = !pg || !_phFitAllowed();
  b.setAttribute('aria-pressed', fit ? 'true' : 'false');
  b.setAttribute('aria-label', fit ? 'โหมดแสดงผล พอดีจอ (แตะเพื่อเปลี่ยนเป็นแตะง่าย)' : 'โหมดแสดงผล แตะง่าย (แตะเพื่อเปลี่ยนเป็นพอดีจอ)');
  if (l) l.textContent = fit ? 'พอดีจอ' : 'แตะง่าย';
  b.title = `โหมดแสดงผล: ${fit ? 'พอดีจอ' : 'แตะง่าย'}`;    // up to 430 px the button is the icon alone (index.html): the mode reads here
  const av = document.getElementById('phTbAv'); if (av) av.textContent = _phInitial();
}
function _phFitOff(panel) {
  if (_phFitObs) { _phFitObs.disconnect(); _phFitObs = null; }
  panel.classList.remove('ph-frame', 'ph-measure');
  delete panel.dataset.phFit;
  ['width', 'zoom', 'transform', 'transform-origin', 'margin-bottom', 'margin-right'].forEach(k => panel.style.removeProperty(k));
  panel.querySelectorAll('[data-phf]').forEach(el => { el.style.removeProperty('font-size'); delete el.dataset.phf; });
  panel.querySelectorAll('.ph-tz').forEach(el => { el.classList.remove('ph-tz'); el.style.removeProperty('zoom'); });
  panel.querySelectorAll('.ph-xs, .ph-xv').forEach(el => { el.classList.remove('ph-xs', 'ph-xv'); el.style.removeProperty('max-height'); delete el.dataset.phcap; });
  panel.querySelectorAll('.ph-z100').forEach(_phZoomOff);
  delete panel._phS;
  _phChartsCrisp(panel, 0);                       // PR-MB: the charts go back to the browser's own pixel ratio
  _phFitPanel = null; _phFitKey = '';
}
// the page draws its content after it opens (and again on every filter): the frame follows, debounced
function _phFitWatch(panel) {
  if (_phFitObs) _phFitObs.disconnect();
  let t = null;
  // data-ph-fit: "wait" while a re-fit is pending (the page has drawn something since the last fit), "ok" once the frame has followed it -
  // the frame's readiness, read by the phone tests instead of a fixed wait (an attribute: it does not wake this observer)
  _phFitObs = new MutationObserver(() => { panel.dataset.phFit = 'wait'; clearTimeout(t); t = setTimeout(() => { if (_phFitPanel === panel && document.body.classList.contains('ph-fit')) { phFitLayout(panel); phAnswerSchedule(); } }, 250); });
  _phFitObs.observe(panel, { childList: true, subtree: true, characterData: true });
}
function _phAvail() {
  const ca = document.querySelector('.content-area'); if (!ca) return window.innerWidth;
  const cs = getComputedStyle(ca);
  return Math.max(200, ca.clientWidth - parseFloat(cs.paddingLeft || '0') - parseFloat(cs.paddingRight || '0'));
}
// the page's own content width: the narrowest width at which nothing in it overflows (charts left out: they take any width)
function _phNatural(panel) {
  const keep = { w: panel.style.width, z: panel.style.zoom, t: panel.style.transform };
  panel.style.zoom = ''; panel.style.transform = ''; panel.style.width = 'min-content';
  panel.classList.add('ph-measure');
  let w = 0;
  try { w = Math.ceil(panel.scrollWidth); } catch (e) { w = 0; }
  panel.classList.remove('ph-measure');
  panel.style.width = keep.w; panel.style.zoom = keep.z; panel.style.transform = keep.t;
  return w;
}
function phFitLayout(panel) {
  if (!panel || !panel.isConnected) return;
  // a table / chart shown at 100 % (phZoomToggle) holds its zoom against the frame's scale of that moment: re-fitting the frame
  // under it (a late part of the page drawing) measured the zoomed element too, widened the frame (799 -> 833 px on วิเคราะห์เมนู)
  // and left the 100 % view at 96 % - 12 px text drawn at 11.5 px. The frame is fitted again when the 100 % view closes. Text the page
  // draws while the 100 % view is open still gets its 12 px floor (no width changes: the floor only raises text under 12 px).
  if (panel.querySelector('.ph-z100')) { _phFitFloor(panel); panel.dataset.phFit = 'ok'; return; }
  _phRowNums(panel);                                                                // PR-MB: no row-number column on a phone (before the width is read)
  const avail = _phAvail();
  const cap = Math.floor(avail * _PH_FIT_TEXT / _PH_FIT_FLOOR);                 // wider than this, 12 px text would read under 5.5 px
  const nat = _phNatural(panel) || _PH_FIT_DEF;
  let W = Math.min(Math.max(nat, _PH_FIT_MIN), _PH_FIT_MAX, cap);
  const prev = +panel.dataset.phw || 0;
  if (prev && Math.abs(prev - W) < 12) W = prev;                                    // a few px of difference is not worth a reflow
  panel.dataset.phw = String(W);
  const s = avail / W;
  panel._phS = s;
  panel.style.width = W + 'px';
  if (_phZoomOK) { panel.style.zoom = String(s); panel.style.transform = ''; }
  else {
    panel.style.transform = `scale(${s})`; panel.style.transformOrigin = '0 0';
    panel.style.marginRight = `${-(W * (1 - s))}px`;
    panel.style.marginBottom = `${-(panel.offsetHeight * (1 - s))}px`;
  }
  _phFitTouch(panel, s);
  _phFitTables(panel, s);
  _phFitFloor(panel);
  _phChartsCrisp(panel, s);
  panel.dataset.phFit = 'ok';
}
// PR-MB · a chart in the frame is drawn for the size it is SHOWN at: its canvas holds (drawn width x device pixel ratio) pixels, so it is
// sharp at rest (scale s) and again at 100 % after a double tap (scale 1) - not a scaled bitmap. scale 0 = back to the browser's own ratio.
function _phChartsCrisp(root, scale) {
  if (typeof Chart === 'undefined' || !root) return;
  const dpr = window.devicePixelRatio || 1;
  root.querySelectorAll('canvas').forEach(cv => {
    const ch = Chart.getChart ? Chart.getChart(cv) : null; if (!ch) return;
    const z = cv.closest('.ph-z100') ? 1 : scale;
    const want = z ? +(dpr * z).toFixed(3) : undefined;
    if (ch.options.devicePixelRatio === want) return;
    ch.options.devicePixelRatio = want;
    try { ch.resize(); } catch (e) { /* not drawn yet */ }
  });
}
// the filters, the pickers, the chips and the "?" keep their touch size: zoom 1/s puts them back at 1:1 (zoom only; a transform cannot undo)
function _phFitTouch(panel, s) {
  if (!_phZoomOK) return;
  panel.querySelectorAll(_PH_TZ_SEL).forEach(el => {
    if (el.closest('table') || el.closest('.ph-z100') || el.parentElement.closest('.ph-tz')) return;
    el.classList.add('ph-tz'); el.style.zoom = String(1 / s);
  });
}
// a table that fits its box keeps the page's scroll (its header pins under the top bar); a wider one scrolls inside its box, header and first column pinned.
// A box with its own height cap (max-height + overflow, e.g. เมนูพุ่งผิดปกติ 380 px) taller than the cap keeps scrolling inside that cap, as on a
// desktop; let go, its table would run over the next block of the page (owner 2026-10-24). The cap is read once, before the frame styles the box.
function _phFitTables(panel, s) {
  const ca = document.querySelector('.content-area');
  const h = Math.max(240, Math.floor(((ca ? ca.clientHeight : window.innerHeight) - 16) / s));
  panel.querySelectorAll('table').forEach(tb => {
    if (tb.closest('.ph-z100')) return;
    let box = tb.parentElement;
    while (box && box !== panel) { const ox = getComputedStyle(box).overflowX; if (ox !== 'visible' && ox !== 'clip') break; box = box.parentElement; }
    if (!box || box === panel) {
      if (tb.offsetWidth <= panel.clientWidth + 1) return;
      box = tb.parentElement;
    }
    if (!box.dataset.phcap && !box.classList.contains('ph-xs') && !box.classList.contains('ph-xv')) {   // no class yet: any max-height is the page's own
      const mh = parseFloat(getComputedStyle(box).maxHeight); box.dataset.phcap = mh > 0 ? String(mh) : '0';
    }
    const cap = +box.dataset.phcap || 0;
    const wide = tb.scrollWidth > box.clientWidth + 1 || box.classList.contains('ph-xs') && tb.scrollWidth > box.clientWidth - 1;
    const tall = cap > 0 && tb.offsetHeight > cap + 1;
    box.classList.toggle('ph-xs', wide || tall); box.classList.toggle('ph-xv', !wide && !tall);
    if (wide || tall) box.style.maxHeight = (cap > 0 ? Math.min(cap, h) : h) + 'px'; else box.style.removeProperty('max-height');
  });
}
// the text floor: every text inside the frame is at least 12 px (so >= 5.5 px at rest, >= 12 px at 100 %); SVG text by its drawn size
function _phFitFloor(panel) {
  const s = panel._phS || 1;
  panel.querySelectorAll('*').forEach(el => {
    if (el.dataset && el.dataset.phf) return;
    const svg = el instanceof SVGElement;
    if (svg && !/^(text|tspan)$/i.test(el.tagName)) return;
    let own = false;
    for (const n of el.childNodes) { if (n.nodeType === 3 && n.textContent.trim()) { own = true; break; } }
    if (!own) return;
    const fs = parseFloat(getComputedStyle(el).fontSize) || 0;
    if (!svg) { if (fs && fs < _PH_FIT_TEXT) { el.style.setProperty('font-size', _PH_FIT_TEXT + 'px', 'important'); el.dataset.phf = '1'; } return; }
    try {
      const m = el.getScreenCTM(); if (!m) return;
      let drawn = fs * Math.hypot(m.b, m.d) * (_phZoomOK ? 1 : s);            // getScreenCTM is in screen px; a transform frame scales on top
      if (el.closest('.ph-z100')) { if (!_phZoomOK) return; drawn *= s; }   // drawn inside a 100 % view: its size at rest is s x that
      if (drawn && drawn < _PH_FIT_FLOOR) { el.style.setProperty('font-size', (fs * _PH_FIT_FLOOR / drawn + 0.05).toFixed(2) + 'px', 'important'); el.dataset.phf = '1'; }
    } catch (e) { /* not drawn */ }
  });
}

// ── a double tap on a table or a chart: that element at 100 % (net zoom 1), the width of the screen, scrolling inside itself ──
function _phZoomTarget(t) {
  const el = t.closest && (t.closest('table') || t.closest('canvas') || t.closest('.heatmap-wrap'));
  if (!el || !el.closest('.ph-frame')) return null;
  const z = el.closest('.ph-z100'); if (z) return z;
  return el.closest('.card') || el.closest('.ph-xs, .ph-xv') || el.parentElement;
}
function phZoomToggle(el) {
  const panel = el.closest('.ph-frame'); if (!panel) return;
  if (el.classList.contains('ph-z100')) { _phZoomOff(el); phFitLayout(panel); return; }
  panel.querySelectorAll('.ph-z100').forEach(_phZoomOff);
  const s = panel._phS || 1, avail = _phAvail(), pr = panel.getBoundingClientRect(), er = el.getBoundingClientRect();
  const ca = document.querySelector('.content-area');
  el.classList.add('ph-z100');
  if (_phZoomOK) {
    el.style.zoom = String(1 / s);
    el.style.width = avail + 'px';
    el.style.marginLeft = `${-(er.left - pr.left)}px`;
    el.style.maxHeight = Math.max(240, (ca ? ca.clientHeight : window.innerHeight) - 24) + 'px';
  } else {
    el.style.transform = `scale(${1 / s})`; el.style.transformOrigin = '0 0';
  }
  el.querySelectorAll('.ph-xs, .ph-xv').forEach(x => { x.classList.remove('ph-xs', 'ph-xv'); x.style.removeProperty('max-height'); });
  el.querySelectorAll('.ph-tz').forEach(x => { x.classList.remove('ph-tz'); x.style.removeProperty('zoom'); });   // at 100 % a control is at its own size already
  _phChartsCrisp(el, 1);                                                             // PR-MB: the chart is redrawn for 100 %, not stretched
  setTimeout(() => { try { el.scrollIntoView({ block: 'start' }); } catch (e) { /* off screen */ } }, 0);
}
function _phZoomOff(el) {
  el.classList.remove('ph-z100');
  ['zoom', 'width', 'margin-left', 'max-height', 'transform', 'transform-origin'].forEach(k => el.style.removeProperty(k));
}

// ── a tap on a table row (พอดีจอ): the row sheet - every column as label / value, the row's status first, its own actions last ──
let _phTap = null, _phRowBypass = false, _phRowActs = [], _phRowOwn = null;
const _PH_INTERACTIVE = 'a[href], a[onclick], button, input, select, textarea, label, summary, [role="button"], [contenteditable="true"], .ph-tz';
function _phRowOf(t) {
  const tr = t.closest && t.closest('tr');
  if (!tr || !tr.closest('.ph-frame') || tr.closest('thead') || !tr.cells || !tr.cells.length) return null;
  if ([...tr.cells].every(c => c.tagName === 'TH')) return null;
  return tr;
}
document.addEventListener('click', e => {
  if (_phRowBypass || !document.body.classList.contains('ph-fit')) return;
  const t = /** @type {Element} */ (e.target);
  if (!t || !t.closest || !t.closest('.ph-frame')) return;
  if (t.closest(_PH_INTERACTIVE)) return;                       // a button, a link, a field of the row does its own job
  const zt = _phZoomTarget(t), row = _phRowOf(t);
  if (!zt && !row) return;
  const now = Date.now();
  if (_phTap && now - _phTap.t < 350 && Math.abs(e.clientX - _phTap.x) < 40 && Math.abs(e.clientY - _phTap.y) < 40) {
    clearTimeout(_phTap.timer); _phTap = null;
    e.preventDefault(); e.stopPropagation();
    if (zt) phZoomToggle(zt);
    return;
  }
  if (row) { e.preventDefault(); e.stopPropagation(); }          // the row's own click waits: it is an action of the sheet
  const tap = { t: now, x: e.clientX, y: e.clientY, timer: null };
  if (row) tap.timer = setTimeout(() => { if (_phTap === tap) _phTap = null; phRowSheet(row); }, 350);
  _phTap = tap;
}, true);
// PR-TD · a cell drawn as lines: each [data-ph-line] element is one line (k = its first part, v = its last part when it has two;
// data-ph-line="hi" = bad ink). The row sheet and the card's grey lines draw them one per line instead of one run of text.
function _phKvLines(td) {
  if (!td || !td.querySelector('[data-ph-line]')) return [];
  return [...td.querySelectorAll('[data-ph-line]')].map(el => {
    const parts = [...el.children].map(x => _phNorm(x.textContent)).filter(Boolean);
    return parts.length === 2 ? { k: parts[0], v: parts[1], hi: el.dataset.phLine === 'hi' } : { k: _phNorm(el.textContent), v: '', hi: el.dataset.phLine === 'hi' };
  }).filter(l => l.k || l.v);
}
function _phKvHtml(lines, cls) {
  return `<span class="${cls}">${lines.map(l => `<span class="${cls}-r${l.hi ? ' ph-kv-hi' : ''}"><span>${esc(l.k)}</span>${l.v ? ` <span class="${cls}-v">${esc(l.v)}</span>` : ''}</span>`).join('')}</span>`;
}
function _phCellText(td) {
  const fields = [...td.querySelectorAll('input:not([type="hidden"]), select, textarea')];
  const own = (td.innerText || td.textContent || '').replace(/\s+/g, ' ').trim();
  const vals = fields.map(f => f.type === 'checkbox' || f.type === 'radio' ? (f.checked ? 'ใช่' : 'ไม่ใช่')
    : f.tagName === 'SELECT' ? ((f.selectedOptions && f.selectedOptions[0] && f.selectedOptions[0].text) || '') : (f.value || '')).filter(Boolean);
  return [own, ...vals].filter(Boolean).join(' · ');
}
function _phHeads(table, n) {
  const rows = table.tHead ? [...table.tHead.rows] : [];
  const out = new Array(n).fill('');
  rows.forEach(r => { let i = 0; [...r.cells].forEach(c => { const span = c.colSpan || 1, t = (c.innerText || c.textContent || '').replace(/\s+/g, ' ').trim(); for (let k = 0; k < span && i < n; k++, i++) if (t) out[i] = span > 1 && out[i] ? out[i] : t; }); });
  return out;
}
// title (PR-MB): the name a card shows; else the row's first value
function phRowSheet(tr, title) {
  const sh = document.getElementById('msSheet'), body = document.getElementById('msSheetBody'), table = tr.closest('table');
  if (!sh || !body || !table) return;
  const cells = [...tr.cells];
  const n = cells.reduce((a, c) => a + (c.colSpan || 1), 0);
  const heads = _phHeads(table, n);
  const pairs = []; let i = 0;
  // a cell that only holds a selection box (the row's tick) is not a value of the row; nor is a row number (#, ลำดับ - PR-MB)
  const tickOnly = c => !(c.innerText || c.textContent || '').trim() && c.querySelector('input[type="checkbox"], input[type="radio"]') && !c.querySelector('input:not([type="checkbox"]):not([type="radio"]), select, textarea');
  cells.forEach(c => { const rn = c.classList.contains('ph-rn') || _PH_RN_RE.test(_phNorm(heads[i])); const v = tickOnly(c) || rn ? '' : _phCellText(c); if (v) pairs.push([heads[i] || '', v, _phKvLines(c)]); i += c.colSpan || 1; });
  const pill = tr.querySelector('.bmc-chip, .wt-tag, .pill, .ms-chip, .frc-pill, .fm-chip, .badge-up, .badge-down, [class*="chip"], [class*="tag-"]');
  _phRowActs = [...tr.querySelectorAll('button, a[href], a[onclick], [role="button"]')].filter(b => (b.innerText || b.getAttribute('aria-label') || b.title || '').trim());
  const own = tr.hasAttribute('onclick') || [...tr.cells].some(c => c.hasAttribute('onclick'));
  const acts = _phRowActs.map((b, k) => `<button type="button" onclick="phRowAct(${k})"${b.disabled ? ' disabled' : ''}>${esc((b.innerText || b.getAttribute('aria-label') || b.title).replace(/\s+/g, ' ').trim())}</button>`);
  if (own) acts.unshift(`<button type="button" onclick="phRowAct(-1)">เปิดรายละเอียด</button>`);
  _phRowOwn = own ? tr : null;
  document.getElementById('msSheetTitle').textContent = (title || (pairs.length ? pairs[0][1] : '') || 'รายการ').slice(0, 80);
  document.getElementById('msSheetSub').textContent = (document.getElementById('pageTitle') || {}).textContent || '';
  body.innerHTML = `<div class="ph-rs">${pill ? `<div class="ph-rs-pill">${pill.outerHTML}</div>` : ''}<dl class="ph-rs-list">`
    + pairs.map(([h, v, kv]) => `<div class="ph-rs-row"><dt>${esc(h || '—')}</dt><dd>${kv.length ? _phKvHtml(kv, 'ph-rs-kv') : esc(v)}</dd></div>`).join('') + '</dl>'
    + (acts.length ? `<div class="ph-rs-act">${acts.join('')}</div>` : '') + '</div>';
  sh.classList.add('open', 'ph-rowsheet'); sh.scrollTop = 0;
  const back = document.getElementById('phSheetBack'); if (back) back.hidden = false;
  const bk = sh.querySelector('.ms-header .ms-toggle'); if (bk) { bk.setAttribute('aria-label', 'ปิด'); try { bk.focus({ preventScroll: true }); } catch (e) { /* no focus */ } }
}
function phRowAct(k) {
  const el = k < 0 ? _phRowOwn : _phRowActs[k];
  msCloseSheet();
  if (!el || !el.isConnected) return;
  _phRowBypass = true;
  try { el.click(); } finally { _phRowBypass = false; }
}

// ════════════════════════════════════════════════════════════════
// CARD LISTS (PR-MB, 2026-10-24)
// ════════════════════════════════════════════════════════════════
// In แตะง่าย (touch) the tables people read daily are one card per row (phCardList): the status / class pill (colour = meaning), the name, a
// grey line (code / branch / date), the three key figures on one line, an optional sparkline (msColumns, B1) and a chevron that opens the row
// sheet with every column. Sort chips (the key figures + the name) and filter chips (the pill's values) sit on top; finished rows are behind the
// shared state filter (uiStateFilter), hidden by default. The tables and their key columns are registered in PAGE_CARDS (js/01-core.js).
// The table stays in the page, hidden while its cards show: the sheet reads its row, the card's actions click the row's own buttons.
// In พอดีจอ and on a desktop the table is the table. Row-number columns (#, ลำดับ) are hidden on a phone in both modes.
const _PH_CL_PAGE = 50;                                // cards drawn at once; "แสดงอีก" draws the next 50
const _PH_RN_RE = /^(#|ลำดับ|ลำดับที่|ที่|อันดับ|no\.?)$/i;   // the header of a row-number column
const _PH_CL_TONE = { good: 'good', bad: 'fix', warn: 'watch', info: 'neu', neutral: 'neu' };
let _phCl = {}, _phClObs = null, _phClPanel = null, _phClT = null;
const _phNorm = s => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
// the group a pill belongs to for the filter chips: its words before a count, a date or a reference (สาขาแก้ไข 2 รายการ -> สาขาแก้ไข)
const _phPillKey = t => (String(t || '').split(/\s+·\s+/)[0].replace(/\s*[+±−-]?\d[\d,./]*.*$/, '').trim()) || String(t || '');
const _phHeadName = s => _phNorm(s).replace(/[\s↕▾▴▲▼⇅↑↓]+$/, '');
const _phTidy = s => s.replace(/^[▸▾►▼›]\s*/, '').replace(/\s*(?:›|→|เจาะลึก\s*→?)$/, '').trim();
// a number as the table prints it: ฿1,234 · 12.5% · −3 · (1,200) · 1.2K · 3.40M · 2 ล้าน -> a number to sort by (null = no number)
function _phNum(t) {
  const s = String(t == null ? '' : t).replace(/[−–]/g, '-').replace(/,/g, '');
  const m = s.match(/(\(?)(-?)\s*[฿$]?\s*(-?)\s*(\d+(?:\.\d+)?)\s*(K(?![a-z])|M(?![a-z])|ล้าน|พัน)?/i);
  if (!m) return null;
  let v = parseFloat(m[4]);
  const u = (m[5] || '').toUpperCase();
  if (u === 'K' || u === 'พัน') v *= 1e3; else if (u === 'M' || u === 'ล้าน') v *= 1e6;
  const neg = m[2] === '-' || m[3] === '-' || /▼/.test(s.slice(0, m.index + 1)) || (m[1] === '(' && /\)/.test(s.slice(m.index)));
  return neg ? -v : v;
}
// the app's own date text on a phone: 2026-10-19 -> 19/10 (the year only when it is not this year)
function _phDateTh(t) {
  const y = String(new Date().getFullYear());
  return String(t == null ? '' : t).replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (a, yy, mm, dd) => `${+dd}/${+mm}${yy === y ? '' : '/' + yy.slice(2)}`);
}
// the column of a header: a number is the column's index (negative from the end); a text is the header as the table prints it
// (exact, then the start of it, then anywhere in it; case and spacing ignored). -1 = the table has no such column.
function _phColIdx(heads, ref) {
  if (typeof ref === 'number') return ref < 0 ? heads.length + ref : ref;
  const r = _phNorm(ref).toLowerCase(), h = heads.map(x => _phNorm(x).toLowerCase());
  let i = h.indexOf(r);
  if (i < 0) i = h.findIndex(x => x.startsWith(r));
  if (i < 0) i = h.findIndex(x => x.includes(r));
  return i;
}
// The card list of one table, as HTML. spec = { id, rows: [{ title, sub, figs: [{ l, v, ink }], pill: { t, tone, html }, done, spark, total }],
// keys: [label x3], sort: { k: 'name' | 0..2, dir: 1 | -1 } | null, filter: '' | pill text, state: 'todo' | 'done' | 'all', hasDone,
// limit, empty: { t, act } }. Pure: the rows are already read (see _phCardSpec); the DOM is not touched.
function phCardList(spec) {
  const id = esc(spec.id), all = spec.rows || [];
  const s = spec.sort, ordered = !!(s && (s.k === 'name' || typeof s.k === 'number'));
  const totals = all.filter(r => r.total && !r.grp), body = all.filter(r => !r.total && !r.grp);
  const st = spec.hasDone ? (spec.state || 'todo') : 'all';
  const byState = body.filter(r => st === 'all' || (st === 'done' ? r.done : !r.done));
  const pills = [];
  byState.forEach(r => { const t = r.pill && r.pill.k; if (t && !pills.includes(t)) pills.push(t); });
  const filter = spec.filter && pills.includes(spec.filter) ? spec.filter : '';
  let rows = byState.filter(r => !filter || (r.pill && r.pill.k === filter));
  if (!ordered && !filter && st === 'all') {                                      // the table's own order keeps its group rows
    const keep = new Set(rows);
    rows = all.filter(r => !r.total && (r.grp || keep.has(r)));
  }
  if (ordered) {
    rows = rows.slice().sort((a, b) => {
      if (s.k === 'name') return s.dir * String(a.title).localeCompare(String(b.title), 'th');
      const x = a.figs[s.k] ? a.figs[s.k].n : null, y = b.figs[s.k] ? b.figs[s.k].n : null;
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return s.dir * (x - y);
    });
  }
  const chip = (on, js, label, n) => `<button type="button" class="ph-cl-chip" aria-pressed="${on}" onclick="${js}">${esc(label)}${n != null ? ` <span class="ph-cl-n">${n}</span>` : ''}</button>`;
  const arrow = k => s && s.k === k ? (s.dir > 0 ? ' ↑' : ' ↓') : '';
  const sorts = [chip(!!(s && s.k === 'name'), `phCardSort('${id}','name')`, 'ชื่อ' + arrow('name'))]
    .concat((spec.keys || []).map((l, k) => chip(!!(s && s.k === k), `phCardSort('${id}',${k})`, l + arrow(k))));
  const filters = pills.length >= 2 && pills.length <= 10
    ? [chip(!filter, `phCardFilter('${id}','')`, 'ทั้งหมด', byState.length)].concat(pills.map(p => chip(filter === p, `phCardFilter('${id}',${esc(JSON.stringify(p))})`, p, byState.filter(r => r.pill && r.pill.k === p).length)))
    : [];
  // phone review item 3 (owner 2026-10-31): the pill is a chip of at most 6 characters that never wraps (uiChipShort); its full words are its title
  const pillHtml = p => {
    if (!p || !p.t) return '';
    const s = uiChipShort(p.t), full = s !== p.t ? ` title="${esc(p.t)}"` : '';
    return `<span class="ph-cl-pill" data-tone="${_PH_CL_TONE[p.tone] || 'neu'}"${full}>${esc(s)}</span>`;
  };
  const tick = r => r.tick ? `<label class="ph-cl-tick"><input type="checkbox" aria-label="${esc(r.tick.l)}"${r.tick.on ? ' checked' : ''}${r.tick.dis ? ' disabled' : ''} onchange="phCardTick('${id}',${r.i},this.checked)"></label>` : '';
  // the key figure of line 1: the figure the list is sorted by, else the first one; the others are short facts on line 2
  const kk = ordered && typeof s.k === 'number' ? s.k : 0;
  // TWO fixed lines (phone review item 3): line 1 = the name (ellipsis) + the ONE key figure (bold, right, tabular); line 2 = the status chip + one
  // grey line (code / branch / date, then the other figures as "label value") + the sparkline as a thin strip. Every column is behind the tap (the sheet).
  const card = (r, i, tot) => {
    const figs = (r.figs || []).filter(f => f.v || !r.grp), has = f => f && f.v && f.v !== '—';
    // unsorted: the first figure the row has (an empty "—" gives way to the next); sorted: the sort's figure, even when empty
    const key = !figs.length ? null : ordered && typeof s.k === 'number' ? figs[Math.min(kk, figs.length - 1)] : (figs.find(has) || figs[0]);
    const rest = figs.filter(f => f !== key && f.v && f.v !== '—').map(f => `${f.l} ${f.v}${f.sub && f.sub.b ? ` (${f.sub.b})` : ''}`);   // figSub: the base stays beside its figure
    const kv = (r.kv || []).map(l => (l.v ? `${l.k} ${l.v}` : l.k));
    // figSub (owner 2026-10-25): the key figure's change and base ("▲0.9% · เดิม 193,477") lead the grey line
    const ks = key && key.sub ? [key.sub.a, key.sub.b].filter(Boolean).join(' ') : '';
    const sb = [(ordered || filter) && r.group ? r.group : '', ks, r.sub, ...kv, ...rest].filter(Boolean).join(' · ');
    return `<li class="ph-cl-card${tot || r.grp ? ' ph-cl-tot' : ''}${r.tick ? ' ph-cl-hastick' : ''}">${tick(r)}<button type="button" class="ph-cl-hit" onclick="phCardOpen('${id}',${r.i})" aria-label="${esc(r.title)}${key ? ` · ${esc(key.l)} ${esc(key.v || '—')}` : ''} · ดูทุกคอลัมน์">`
      + `<span class="ph-cl-main"><span class="ph-cl-l1"><span class="ph-cl-t">${esc(r.title || '—')}</span>`
      + (key ? `<span class="ph-cl-f" data-l="${esc(key.l)}"><span class="ph-cl-fv"${key.ink ? ` style="color:${esc(key.ink)}"` : ''}>${esc(key.v || '—')}</span></span>` : '') + '</span>'
      + (sb || r.pill || r.spark ? `<span class="ph-cl-l2">${pillHtml(r.pill)}${sb ? `<span class="ph-cl-s">${esc(sb)}</span>` : ''}${r.spark ? `<span class="ph-cl-spark">${r.spark}</span>` : ''}</span>` : '')
      + `</span><span class="ph-cl-chev" aria-hidden="true">›</span></button></li>`;
  };
  const lim = Math.max(_PH_CL_PAGE, spec.limit || 0);
  const shown = rows.slice(0, lim);
  // DV5: a ranked list shows its first rows (cap.top) and, for a two-sided list, its last rows (cap.bottom); the rest are behind "ดูทั้งหมด (N)"
  const cp = spec.cap, capOn = !!(cp && !spec.open && rows.length > cp.top + (cp.bottom || 0) + 2);
  const capBtn = cp && rows.length > cp.top + (cp.bottom || 0) + 2
    ? `<div class="dv-more-wrap"><button type="button" class="shm-showall dv-more ph-cl-all" aria-expanded="${spec.open ? 'true' : 'false'}" onclick="phCardAll('${id}',${spec.open ? 'false' : 'true'})">${spec.open ? 'ย่อ' : `ดูทั้งหมด (${fmtN(rows.length)})`}</button></div>` : '';
  let list;
  if (!all.length || (!rows.length && !totals.length)) {
    const e = spec.empty || {};
    const stEmpty = spec.hasDone && body.length && st !== 'all';
    if (e.html && !stEmpty && !filter) list = e.html;                     // item 7: the page's own one card (icon, why, the fix)
    else list = `<div class="ph-cl-empty" role="status"><span>${esc(stEmpty ? (st === 'done' ? 'ยังไม่มีรายการที่เสร็จแล้ว' : 'ไม่มีรายการค้าง') : filter ? 'ไม่มีรายการในกลุ่มนี้' : (e.t || 'ไม่มีรายการในช่วงที่เลือก'))}</span>`
      + (stEmpty ? `<button type="button" class="ph-cl-act" onclick="phCardState('all')">ดูทั้งหมด</button>`
        : filter ? `<button type="button" class="ph-cl-act" onclick="phCardFilter('${id}','')">ดูทั้งหมด</button>`
          : `<button type="button" class="ph-cl-act" onclick="phCardEmptyAct('${id}')">${esc(e.act || 'เลือกช่วงอื่น')}</button>`) + '</div>';
  } else if (capOn) {
    const top = rows.slice(0, cp.top), bot = cp.bottom ? rows.slice(rows.length - cp.bottom) : [], gap = rows.length - top.length - bot.length;
    list = `<ul class="ph-cl-list" role="list">${totals.map(r => card(r, 0, true)).join('')}${top.map((r, i) => card(r, i, false)).join('')}`
      + (bot.length ? `<li class="ph-cl-gap" aria-hidden="true">··· อีก ${fmtN(gap)} รายการ ···</li>${bot.map((r, i) => card(r, i, false)).join('')}` : '') + '</ul>' + capBtn;
  } else {
    list = `<ul class="ph-cl-list" role="list">${totals.map(r => card(r, 0, true)).join('')}${shown.map((r, i) => card(r, i, false)).join('')}</ul>`
      + (rows.length > lim ? `<button type="button" class="ph-cl-more" onclick="phCardMore('${id}')">แสดงอีก ${Math.min(_PH_CL_PAGE, rows.length - lim)} จาก ${rows.length - lim} รายการ</button>` : '') + capBtn;
  }
  return `<div class="ph-cl" data-cl="${id}">`
    + (spec.hasDone && body.length ? `<div class="ph-cl-st" data-cl-st="${id}"></div>` : '')
    + (all.length > 1 ? `<div class="ph-cl-chips" role="group" aria-label="เรียงตาม">${sorts.join('')}</div>` : '')
    + (filters.length ? `<div class="ph-cl-chips" role="group" aria-label="กรองตามกลุ่ม">${filters.join('')}</div>` : '')
    + list + '</div>';
}
// the colour a cell's text carries in the table (an inline colour or a text-* class), so a figure keeps its meaning on the card
function _phInk(td) {
  for (const el of [td, ...td.querySelectorAll('*')]) {
    if (el.style && el.style.color) return el.style.color;
    const c = [...(el.classList || [])].find(x => /^text-(red|rose|green|emerald|amber|orange|yellow)-\d00$/.test(x));
    if (c) return /red|rose/.test(c) ? 'var(--bad-ink)' : /green|emerald/.test(c) ? 'var(--good-ink)' : 'var(--amber-ink)';
  }
  return '';
}
const _PH_CHIP_SEL = '.bmc-chip, .wt-tag, .pill, .ms-chip, .frc-pill, .fm-chip, .badge-up, .badge-down, .ohb-st, [class*="chip"], [class*="tag-"], [class*="badge"]';
// The card spec of one table as it is drawn: its header texts, its body rows (a message row such as "ไม่มีข้อมูล" is the empty state),
// the columns PAGE_CARDS names. cfg = { title, sub, keyCols, pill, tones, done, spark, empty, total, subLines }.
// subLines (PR-TD): a sub column drawn as lines (data-ph-line, _phKvLines) shows one line each under the grey line instead of one run of text.
// the lines of a cell as it is drawn (a <br>, a <div>, a <p> start a line) - read from a copy, so a hidden table reads the same as a shown one
function _phLines(el) {
  const c = /** @type {Element} */ (el.cloneNode(true));
  c.querySelectorAll('br').forEach(b => b.replaceWith('\n'));
  c.querySelectorAll('div, p, li').forEach(d => d.prepend('\n'));
  c.querySelectorAll('script, style, template').forEach(x => x.remove());
  return String(c.textContent || '').split('\n').map(_phNorm).filter(Boolean);
}
const _PH_MONTH_HEAD = /^(ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)|^\d{4}-\d{2}$/;
function _phCardSpec(table, cfg, id) {
  const rowSel = cfg.rows || '';
  const bodyRows = [...table.tBodies].flatMap(b => [...b.rows]).filter(tr => tr.cells.length && tr.style.display !== 'none' && !tr.hidden && !tr.classList.contains('hidden')
    && (!rowSel || tr.matches(rowSel) || tr.cells.length === 1) && ![...tr.cells].every(c => c.tagName === 'TH' && (c.colSpan || 1) > 1));
  const n = Math.max(1, ...bodyRows.map(tr => [...tr.cells].reduce((a, c) => a + (c.colSpan || 1), 0)));
  const heads = _phHeads(table, n);
  const col = ref => Array.isArray(ref) ? ref.map(r => _phColIdx(heads, r)).find(i => i >= 0) ?? -1 : _phColIdx(heads, ref);
  const ti = col(cfg.title != null ? cfg.title : 0), pi = cfg.pill != null ? col(cfg.pill) : -1;
  const ki = (cfg.keyCols || []).map(col).filter(i => i >= 0).slice(0, 3), si = (cfg.sub || []).map(col);
  const spi = cfg.spark === 'months' ? heads.map((h, i) => _PH_MONTH_HEAD.test(_phNorm(h)) ? i : -1).filter(i => i >= 0)
    : Array.isArray(cfg.spark) ? cfg.spark.map(col).filter(i => i >= 0) : [];
  const sci = cfg.sparkCell != null ? col(cfg.sparkCell) : -1;
  const cellsOf = tr => { const out = []; [...tr.cells].forEach(c => { for (let k = 0; k < (c.colSpan || 1); k++) out.push(c); }); return out; };
  const tones = Object.entries(cfg.tones || {}).map(([re, t]) => [new RegExp(re, 'i'), t]);
  const doneRe = cfg.done ? new RegExp(cfg.done, 'i') : null;
  const lines = td => td ? _phLines(td) : [];
  const txt = td => td ? _phNorm(td.textContent) : '';
  let empty = null, emptyHtml = '', group = '';
  const rows = [];
  bodyRows.forEach(tr => {
    const cs = cellsOf(tr);
    if (tr.cells.length === 1 && (tr.cells[0].colSpan || 1) >= Math.max(2, n - 1)) {                    // "ไม่มีข้อมูล"
      const own = tr.cells[0].querySelector('.ui-empty');                  // item 7: the page's own empty card is drawn as it is
      if (own && !emptyHtml) emptyHtml = own.outerHTML;
      empty = empty || (own ? _phNorm((own.querySelector('.ui-empty-t') || own).textContent) : txt(tr.cells[0])); return;
    }
    const isGrp = (tr.cells[0].colSpan || 1) >= 3;                                                     // a group's row (a branch above its cashiers)
    if (isGrp) group = txt(tr.cells[0]);
    // the name: the cell's own text without its buttons, chips and fields (a cell that IS a button keeps it), the toggle arrows cut off
    const tcell = cs[ti] || cs[0];
    let tl = [];
    let tchips = [];
    if (tcell) {
      const cl = /** @type {HTMLElement} */ (tcell.cloneNode(true));
      tchips = [...cl.querySelectorAll(_PH_CHIP_SEL)].map(x => x.outerHTML);
      cl.querySelectorAll('button, input, select, textarea, ' + _PH_CHIP_SEL).forEach(x => x.remove());
      tl = _phLines(cl);
      if (!tl.length) tl = lines(tcell);
      if (!tl.length) { const v = _phCellText(tcell); if (v) tl = [v]; }
    }
    tl = tl.map(_phTidy).filter(Boolean);
    const title = tl[0] || '';
    const kv = cfg.subLines ? si.flatMap(i => (i >= 0 && cs[i] && cs[i] !== tcell ? _phKvLines(cs[i]) : [])) : [];
    const sub = [...tl.slice(1), ...si.map((i, k) => {
      if (i < 0 || !cs[i] || cs[i] === tcell || kv.length) return '';
      const v = _phTidy(lines(cs[i]).join(' · '));
      return v && cfg.subL ? `${(cfg.subLabels && cfg.subLabels[k]) || _phNorm(heads[i])} ${v}` : v;
    })].filter(Boolean);
    // figSub (owner 2026-10-25): a figure cell drawn as [data-ph-line] lines shows its second line under the figure (the change and the
    // base value, "▲0.9% · เดิม 193,477"): its first part keeps its own ink, the figure takes only the first line's ink
    const figs = ki.map((i, k) => {
      const td = cs[i] && cs[i] !== tcell ? cs[i] : null, v = td ? (lines(td)[0] || '') || _phCellText(td) : '';
      const ls = cfg.figSub && td ? [...td.querySelectorAll('[data-ph-line]')] : [];
      let sub = null;
      if (ls[1]) {
        const ch = [...ls[1].children], a = ch[0] && /** @type {HTMLElement} */ (ch[0]).style.color ? /** @type {HTMLElement} */ (ch[0]) : null;
        sub = a ? { a: _phNorm(a.textContent), ink: a.style.color, b: _phNorm(ch.slice(1).map(x => x.textContent).join(' ')).replace(/^·\s*/, '') } : { a: '', ink: '', b: _phNorm(ls[1].textContent) };
      }
      return { l: (cfg.labels && cfg.labels[k]) || _phHeadName(heads[i]), v: _phDateTh(v), n: v ? _phNum(v) : null,
        ink: td ? (ls[0] ? _phInk(ls[0]) || td.style.color || '' : _phInk(td)) : '', sub };
    });
    let pill = null;
    const pcell = pi >= 0 ? cs[pi] : null;
    const chips = pcell ? [...pcell.querySelectorAll(_PH_CHIP_SEL)].filter(x => !x.parentElement.closest(_PH_CHIP_SEL)) : [];
    const pfirst = pcell && !chips.length ? [...pcell.querySelectorAll('span, b, strong')].find(x => _phNorm(x.textContent)) : null;
    const ptxt = (chips.length ? _phNorm(chips[0].textContent) : pfirst && _phNorm(pfirst.textContent).length <= 30 ? _phNorm(pfirst.textContent)
      : pcell ? (lines(pcell)[0] || '') : '').slice(0, 40);
    // phone review item 3: the pill is ONE short chip (uiChipShort in phCardList); a page's own chip gives its meaning through its ink
    const inkTone = el => { const c = el ? String(el.getAttribute('style') || '') + ' ' + el.className : '';
      return /bad-ink|fix-|red|rose/.test(c) ? 'bad' : /good-ink|good-|green|emerald/.test(c) ? 'good' : /amber|watch-|orange|yellow/.test(c) ? 'warn' : ''; };
    if (ptxt) {
      const tone = (tones.find(([re]) => re.test(ptxt)) || [])[1] || (chips.length ? inkTone(chips[0]) : '');
      pill = { t: ptxt, k: _phPillKey(uiChipShort(ptxt)), tone: tone || 'neutral' };
    } else if (pi < 0 && tchips.length) {
      const d = document.createElement('div'); d.innerHTML = tchips[0];
      const t0 = _phNorm(d.textContent);
      pill = { t: t0, k: _phPillKey(uiChipShort(t0)), tone: inkTone(/** @type {HTMLElement} */ (d.firstElementChild)) || 'neutral' };
    }
    const tickCell = [...tr.cells].find(c => !_phNorm(c.textContent) && c.querySelector('input[type="checkbox"]') && !c.querySelector('input:not([type="checkbox"]), select, textarea'));
    const tick = tickCell ? /** @type {HTMLInputElement} */ (tickCell.querySelector('input[type="checkbox"]')) : null;
    const sp = spi.length >= 3 ? spi.map(i => _phNum(txt(cs[i]))) : null;
    const svg = sci >= 0 && cs[sci] ? cs[sci].querySelector('svg') : null;
    const total = cfg.total !== false && /^(รวม|ทั้งหมด|เฉลี่ยเครือ|ทั้งเครือ|ค่ากลาง)/.test(title);
    rows.push({ i: rows.length, tr, title, sub: _phDateTh(sub.join(' · ')), kv, group: isGrp ? '' : group, grp: isGrp, figs, pill, total,
      tick: tick ? { on: !!tick.checked, l: tick.getAttribute('aria-label') || 'เลือก', dis: !!tick.disabled } : null,
      done: !!(doneRe && (doneRe.test(ptxt) || doneRe.test(tr.getAttribute('data-state') || ''))),
      spark: sp && sp.filter(v => v != null).length >= 3 && typeof msColumns === 'function' ? msColumns(sp, { height: 46, fmt: v => fmt(v), aria: 'แนวโน้ม' })
        : svg ? svg.outerHTML : '' });
  });
  const keys = ki.map((i, k) => (cfg.labels && cfg.labels[k]) || _phHeadName(heads[i]));
  // managed (DV5): the page already caps the table itself (DV2's "ดูทั้งหมด", rows with data-dv-more) - the cards follow its rows, no second cap
  return { id, rows, keys, hasDone: !!doneRe, managed: !!table.querySelector('tr[data-dv-more]'),
    empty: { t: (cfg.empty && cfg.empty.t) || empty || '', act: cfg.empty && cfg.empty.act, html: emptyHtml } };
}
// the box of a table that the cards replace: the table and the wrappers that hold nothing else (a scroll box, a .wt-wrap)
function _phClBox(table, panel) {
  let box = table;
  while (box.parentElement && box.parentElement !== panel && box.parentElement.children.length === 1 && !box.parentElement.classList.contains('card')
    && !/^tab-/.test(box.parentElement.id || '')) box = box.parentElement;
  return box;
}
function _phClOn() {
  return phOn() && _phView === 'page' && !document.body.classList.contains('ph-fit');
}
function phCardsSchedule() { clearTimeout(_phClT); _phClT = setTimeout(phCardsApply, 0); }
function phCardsOf(tab, pane) { const r = typeof PAGE_CARDS === 'object' ? PAGE_CARDS : {}; return r[tab + '|' + (pane || '')] || r[tab] || []; }
function _phClOff(scope) {
  (scope || document).querySelectorAll('.ph-cl').forEach(l => l.remove());
  (scope || document).querySelectorAll('.ph-cl-src').forEach(b => b.classList.remove('ph-cl-src'));
}
function phCardsApply() {
  const pg = phCurPage(), on = !!pg && _phClOn();
  const panel = pg ? document.getElementById('tab-' + pg.tab) : null;
  if (_phClPanel && (_phClPanel !== panel || !on)) { _phClOff(_phClPanel); if (_phClObs) { _phClObs.disconnect(); _phClObs = null; } _phClPanel = null; }
  if (!phOn() || !panel) { phAnswerApply(); return; }
  _phRowNums(panel);
  uiCompactFilters(panel);                 // phone review item 8: a filter row is one row of chips, its downloads under "⋯"
  if (!on) { phAnswerApply(); return; }
  if (_phClPanel !== panel) { _phClPanel = panel; _phClWatch(panel); }
  const cfgs = phCardsOf(pg.tab, pg.pane), live = new Set();
  // a table on screen: itself, or (once its cards show) the place of its hidden box; a table of a hidden sub-tab is not
  const shown = t => { const src = t.closest('.ph-cl-src'), el = src ? src.parentElement : t; return !!el && el.getClientRects().length > 0; };
  // a table a writer edits in place (fields in its rows) stays the table: its cards would hide the fields
  const edits = t => [...t.tBodies].some(b => b.querySelector('input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"]), select, textarea'));
  cfgs.forEach((cfg, k) => {
    const tables = [...panel.querySelectorAll(cfg.sel)].filter(t => t.tagName === 'TABLE' && !t.closest('.ph-cl') && shown(t) && !(cfg.fields === 'table' && edits(t)));
    (cfg.all ? tables : tables.slice(0, 1)).forEach((table, j) => {
      const id = `${pg.tab}${pg.pane ? '-' + pg.pane : ''}-${k}-${j}`;
      live.add(id);
      const box = _phClBox(table, panel);
      const sig = table.tBodies.length + ':' + table.rows.length + ':' + (table.textContent || '').length;
      const st = _phCl[id] = _phCl[id] || { sort: null, filter: '', limit: 0 };
      const cur = box.previousElementSibling && box.previousElementSibling.matches('.ph-cl') && box.previousElementSibling.getAttribute('data-cl') === id ? box.previousElementSibling : null;
      if (cur && st.sig === sig && st.table === table) return;
      const spec = _phCardSpec(table, cfg, id);
      if (st.sig !== sig) st.open = false;                                        // new rows (a filter, a period): the list starts capped again
      Object.assign(st, { sig, table, spec, cfg, cap: _phClCap(pg, cfg, spec) });
      _phClDraw(id, box, cur);
    });
  });
  panel.querySelectorAll('.ph-cl').forEach(l => { if (!live.has(l.getAttribute('data-cl'))) { const b = l.nextElementSibling; l.remove(); if (b) b.classList.remove('ph-cl-src'); } });
  phAnswerApply();
}
function _phClDraw(id, box, cur) {
  const st = _phCl[id]; if (!st || !st.spec) return;
  if (!box) { const old = document.querySelector(`.ph-cl[data-cl="${id}"]`); box = old && old.nextElementSibling; cur = old; }
  if (!box) return;
  const html = phCardList(Object.assign({}, st.spec, { sort: st.sort, filter: st.filter, limit: st.limit, state: uiStateFilter.get(), cap: st.cap, open: !!st.open }));
  const wrap = document.createElement('div'); wrap.innerHTML = html;
  const el = wrap.firstElementChild;
  if (cur) cur.replaceWith(el); else box.parentElement.insertBefore(el, box);
  box.classList.add('ph-cl-src');
  const sb = el.querySelector('[data-cl-st]');
  if (sb) {
    const body = st.spec.rows.filter(r => !r.total), done = body.filter(r => r.done).length;
    uiStateFilter.bar(sb, { f: uiStateFilter.get(), counts: { todo: body.length - done, done, all: body.length }, pick: 'phCardState' });
  }
}
// the page draws its rows after it opens (and again on every filter): the cards follow, debounced; their own changes are not a redraw
function _phClWatch(panel) {
  if (_phClObs) _phClObs.disconnect();
  let t = null;
  const own = n => n.nodeType === 1 && (/** @type {Element} */ (n).classList.contains('ph-cl') || /** @type {Element} */ (n).classList.contains('ph-ans'));
  _phClObs = new MutationObserver(ms => {
    const real = ms.some(m => {
      const el = m.target.nodeType === 1 ? /** @type {Element} */ (m.target) : m.target.parentElement;
      if (el && el.closest('.ph-cl, .ph-ans')) return false;
      if (m.type === 'attributes') return m.attributeName !== 'class' || !/ph-(cl|rn)/.test(String(m.oldValue || '') + ' ' + (el ? el.getAttribute('class') : ''));
      return !([...m.addedNodes, ...m.removedNodes].length && [...m.addedNodes, ...m.removedNodes].every(own));
    });
    if (!real) return;
    clearTimeout(t); t = setTimeout(() => { if (_phClPanel === panel) phCardsApply(); }, 200);
  });
  // a sub-tab shown or hidden (style / class / hidden) is a change too; the cards' own class on the table's box is not
  _phClObs.observe(panel, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['style', 'class', 'hidden'], attributeOldValue: true });
}
function phCardSort(id, k) {
  const st = _phCl[id]; if (!st) return;
  const same = st.sort && st.sort.k === k;
  st.sort = { k, dir: same ? -st.sort.dir : (k === 'name' ? 1 : -1) };       // a figure sorts largest first, the name ก-ฮ; again: the other way
  st.limit = 0; _phClDraw(id);
}
function phCardFilter(id, p) { const st = _phCl[id]; if (!st) return; st.filter = p; st.limit = 0; _phClDraw(id); }
function phCardAll(id, open) { const st = _phCl[id]; if (!st) return; st.open = !!open; st.limit = 0; _phClDraw(id);
  if (!open) { const l = document.querySelector(`.ph-cl[data-cl="${id}"]`); if (l) try { l.scrollIntoView({ block: 'nearest' }); } catch (e) { /* off screen */ } } }
function phCardMore(id) { const st = _phCl[id]; if (!st) return; st.limit = Math.max(_PH_CL_PAGE, st.limit || 0) + _PH_CL_PAGE; _phClDraw(id); }
function phCardState(f) { uiStateFilter.set(f); Object.keys(_phCl).forEach(id => { if (document.querySelector(`.ph-cl[data-cl="${id}"]`)) _phClDraw(id); }); }
function phCardOpen(id, i) {
  const st = _phCl[id], r = st && st.spec && st.spec.rows.find(x => x.i === i);
  if (!r || !r.tr || !r.tr.isConnected) { phCardsApply(); return; }
  phRowSheet(r.tr, r.title);
}
// the tick of a card is the row's own tick (a bulk selection): the row's box changes and its own handler runs
function phCardTick(id, i, on) {
  const st = _phCl[id], r = st && st.spec && st.spec.rows.find(x => x.i === i);
  const cb = r && r.tr && r.tr.isConnected ? /** @type {HTMLInputElement} */ (r.tr.querySelector('input[type="checkbox"]')) : null;
  if (!cb) { phCardsApply(); return; }
  cb.checked = !!on;
  cb.dispatchEvent(new Event('change', { bubbles: true }));
  r.tick.on = !!on;
}
// the action of an empty list: the period / filter the page reads (its first picker, else the top bar's filters)
function phCardEmptyAct(id) {
  const l = document.querySelector(`.ph-cl[data-cl="${id}"]`), panel = l && l.closest('.tab-content');
  const f = panel && [...panel.querySelectorAll('select, input[type="month"], input[type="date"], input[type="week"]')].find(x => x.offsetParent !== null && !x.closest('.ph-cl'));
  if (f) { try { f.scrollIntoView({ block: 'center' }); f.focus({ preventScroll: true }); } catch (e) { /* off screen */ } return; }
  if (document.body.classList.contains('tb-folded') && typeof toggleTopbarFold === 'function') { toggleTopbarFold(); return; }
  const tb = document.querySelector('.top-bar select, .top-bar input'); if (tb) { try { /** @type {HTMLElement} */ (tb).focus(); } catch (e) { /* hidden */ } }
}
// a row-number column (#, ลำดับ) says nothing on a phone: its cells are hidden in both modes (the first column only, a single cell wide)
function _phRowNums(panel) {
  panel.querySelectorAll('table').forEach(tb => {
    const h = tb.tHead && tb.tHead.rows[0] && tb.tHead.rows[0].cells[0];
    if (!h || (h.colSpan || 1) !== 1 || !_PH_RN_RE.test(_phNorm(h.textContent))) return;
    [...tb.rows].forEach(tr => {
      const c = tr.cells[0];
      if (!c || c.classList.contains('ph-rn') || (c.colSpan || 1) !== 1 || c.querySelector('input, button, select')) return;
      if (c.tagName === 'TD' && !/^[\d.\s]*$/.test(c.textContent || '')) return;          // a label such as "รวม" in that column stays
      c.classList.add('ph-rn');
    });
  });
}

// ── DV5 (phone table review 2026-10-25) · the answer on the first screen ──────────────────────────────────────────────────────────
// 1) the cap of a card list: an analytics page's ranked list shows its first 10 cards (cfg.cap: a number = that many; 'pair' = the 5 first
//    and the 5 last of a two-sided list; false = every card); "ดูทั้งหมด (N)" opens the rest. Not capped: a to-do list (cfg.done), a writer's
//    table, a list the page caps itself (DV2 data-dv-more), a supply page's work lists, and the lists of the pages being rebuilt in other
//    PRs (_PH_CL_CAP_LATER: they set cfg.cap themselves when they land).
const _PH_CL_TOP = 10, _PH_CL_PAIR = 5;
const _PH_CL_CAP_LATER = { tabs: ['hvmonitor', 'franchise', 'menuactionboard', 'menuopportunities'], sel: /^#spike/ };
function _phClCap(pg, cfg, spec) {
  if (cfg.cap === false || spec.hasDone || spec.managed || cfg.fields) return null;
  if (cfg.cap == null && (_PH_CL_CAP_LATER.tabs.includes(pg.tab) || _PH_CL_CAP_LATER.sel.test(cfg.sel || ''))) return null;
  if (cfg.cap == null && typeof _SUPPLY_PANE === 'object' && _SUPPLY_PANE[pg.tab]) return null;
  if (cfg.cap === 'pair') return { top: _PH_CL_PAIR, bottom: _PH_CL_PAIR };
  return { top: typeof cfg.cap === 'number' ? cfg.cap : _PH_CL_TOP, bottom: 0 };
}
// 2) the answer strip (PAGE_ANSWERS / pageAnswer, js/01-core.js): three lines at the top of the page in แตะง่าย, above its first card. A pushed
//    answer counts only while the filters it was made for are on; a provider is read from the page as drawn, so both follow the top bar's
//    period and branch. In พอดีจอ it sits above the scaled frame at the touch size (coordinator 2026-10-26); the desktop has none.
let _phAnsT = null;
function phAnswerSchedule() { clearTimeout(_phAnsT); _phAnsT = setTimeout(phAnswerApply, 0); }
function _phAnsCtx(pg, panel) {
  const id = k => `${pg.tab}${pg.pane ? '-' + pg.pane : ''}-${k}-0`;
  // the k-th card list of the page as parsed: the drawn cards' spec when they show this very table (แตะง่าย), else read from the table now
  // (พอดีจอ keeps the table and draws no cards)
  const spec = k => {
    const cfg = phCardsOf(pg.tab, pg.pane)[k]; if (!cfg) return null;
    const ts = [...panel.querySelectorAll(cfg.sel)].filter(t => t.tagName === 'TABLE' && !t.closest('.ph-cl'));
    const t = ts.find(x => { const src = x.closest('.ph-cl-src'); return (src ? src.parentElement : x).getClientRects().length > 0; }) || ts[0];
    if (!t) return null;
    const st = _phCl[id(k)], sig = t.tBodies.length + ':' + t.rows.length + ':' + (t.textContent || '').length;
    return st && st.spec && st.table === t && st.sig === sig ? st.spec : _phCardSpec(t, cfg, id(k));
  };
  return { tab: pg.tab, pane: pg.pane, panel,
    rows: k => { const s = spec(k); return s ? s.rows.filter(r => !r.total && !r.grp) : []; },
    total: k => { const s = spec(k); return s ? s.rows.find(r => r.total) || null : null; } };
}
function phAnswerLines(pg, panel) {
  const keys = pg.pane ? [pg.tab + '|' + pg.pane, pg.tab] : [pg.tab];
  for (const key of keys) {
    const push = _pageAns[key];
    if (push) return push.fk === ansFilterKey(push.follow) ? push.lines : [];      // made for other filters: nothing until the page has redrawn
    const fn = PAGE_ANSWERS[key];
    if (typeof fn === 'function') { try { return (fn(_phAnsCtx(pg, panel)) || []).filter(Boolean); } catch (e) { console.warn('answer strip:', e); return []; } }
  }
  return [];
}
function phAnswerHtml(lines) {
  const li = (l, i) => {
    const inner = `<span class="ph-ans-k">${esc(l.k)}</span><span class="ph-ans-v">${esc(l.v)}</span>${l.s ? `<span class="ph-ans-s">${esc(l.s)}</span>` : ''}`;
    return `<li class="ph-ans-i" data-tone="${esc(l.tone || 'neu')}">${l.go
      ? `<button type="button" class="ph-ans-hit" onclick="phAnswerGo(${i})" aria-label="${esc(l.k + ' ' + l.v)} · ดูรายการ">${inner}<span class="ph-ans-go" aria-hidden="true">›</span></button>`
      : `<div class="ph-ans-hit">${inner}</div>`}</li>`;
  };
  return `<ol class="ph-ans-l">${lines.map(li).join('')}</ol>`;
}
// แตะง่าย: the strip is the first block of the page (under its sub-tab bar); พอดีจอ: it sits directly ABOVE the scaled frame, outside it, at the
// touch size (like the top bar and the chips) - one strip per page either way
function phAnswerApply() {
  const pg = phCurPage(), on = !!pg && phOn() && _phView === 'page';
  const fit = document.body.classList.contains('ph-fit');
  const panel = on ? document.getElementById('tab-' + pg.tab) : null;
  const placed = el => fit ? el.nextElementSibling === panel : el.parentElement === panel;
  document.querySelectorAll('.ph-ans').forEach(el => { if (!panel || el.getAttribute('data-tab') !== pg.tab || !placed(el)) el.remove(); });
  if (!panel) return;
  const lines = phAnswerLines(pg, panel).filter(l => l && l.v).slice(0, 3);
  let el = /** @type {HTMLElement|null} */ (fit ? (panel.previousElementSibling && panel.previousElementSibling.matches('.ph-ans') ? panel.previousElementSibling : null)
    : panel.querySelector(':scope > .ph-ans'));
  if (!lines.length) { if (el) el.remove(); return; }
  const html = phAnswerHtml(lines);
  if (el && el.getAttribute('data-sig') === html) return;
  if (!el) {
    el = document.createElement('section');
    el.className = 'ph-ans'; el.setAttribute('aria-label', 'คำตอบของหน้านี้'); el.setAttribute('data-tab', pg.tab);
    if (fit) panel.parentElement.insertBefore(el, panel);
    else {
    let at = panel.firstElementChild;                                     // under the page's own sub-tab bar (and a [data-ans-after] head), above the rest
    while (at && (at.matches('.subtab-bar, [data-ans-after]') || at.tagName === 'TEMPLATE' || at.tagName === 'SCRIPT')) at = at.nextElementSibling;
    panel.insertBefore(el, at);
    }
  }
  el.setAttribute('data-sig', html);
  el.innerHTML = html;
  el._lines = lines;
}
// the providers of pages whose ranked list already answers (DV2 ranked them by baht): the first two cards and one line of the page's
// second list / total. `f(r, k)` = the k-th key figure of a card as the table prints it.
const _ansF = (r, k) => (r && r.figs && r.figs[k] && r.figs[k].v) || '';
const _ansB = (r, k) => { const v = _ansF(r, k); return v && !/฿/.test(v) && /^[+−-]?\d/.test(v) ? (/^[+−-]/.test(v) ? v[0] + '฿' + v.slice(1) : '฿' + v) : v; };
const _ansN = (r, k) => (r && r.figs && r.figs[k] ? r.figs[k].n : null);
PAGE_ANSWERS.cancelbill = c => {
  const b = c.rows(0), rs = c.rows(1).slice().sort((x, y) => (_ansN(y, 2) || 0) - (_ansN(x, 2) || 0));
  return [b[0] && ansLine('มูลค่ายกเลิกมากสุด', `${b[0].title} ${_ansB(b[0], 0)}`, _ansF(b[0], 1) ? `${_ansF(b[0], 1)} ของมูลค่ายกเลิก · ${_ansF(b[0], 2)} บิล` : '', 'bad', '#cbBranches'),
    b[1] && ansLine('อันดับถัดไป', `${b[1].title} ${_ansB(b[1], 0)}`, _ansF(b[1], 1) ? `${_ansF(b[1], 1)} ของมูลค่ายกเลิก` : '', 'bad', '#cbBranches'),
    rs[0] && ansLine('เหตุผลที่มูลค่ามากสุด', `${rs[0].title} ${_ansB(rs[0], 2)}`, _ansF(rs[0], 0) ? `${_ansF(rs[0], 0)} บิล` : '', 'warn', '#cbReasons')];
};
PAGE_ANSWERS.cashctl = c => {
  const b = c.rows(0).filter(r => _ansN(r, 0)), w = c.rows(1);
  const tone = r => (_ansN(r, 0) || 0) < 0 ? 'bad' : 'warn';
  return [b[0] && ansLine('ขาด/เกินมากสุด', `${b[0].title} ${_ansF(b[0], 0)}`, _ansF(b[0], 2) ? `${_ansF(b[0], 2)} ของยอด` : '', tone(b[0]), '#ccBody'),
    b[1] && ansLine('อันดับถัดไป', `${b[1].title} ${_ansF(b[1], 0)}`, _ansF(b[1], 2) ? `${_ansF(b[1], 2)} ของยอด` : '', tone(b[1]), '#ccBody'),
    w[0] && ansLine('วันที่ขาดหนักสุด', `${w[0].title} ${_ansF(w[0], 0)}`, w[0].sub || '', 'bad', '#ccWorst')];
};
PAGE_ANSWERS.gpbranch = c => {
  const b = c.rows(0).filter(r => (_ansN(r, 0) || 0) > 0), t = c.total(0);
  return [b[0] && ansLine('GP ขาดจากเป้ามากสุด', `${b[0].title} ${_ansB(b[0], 0)}`, `GP ${_ansF(b[0], 2)}`, 'bad', '#gpbTable'),
    b[1] && ansLine('อันดับถัดไป', `${b[1].title} ${_ansB(b[1], 0)}`, `GP ${_ansF(b[1], 2)}`, 'bad', '#gpbTable'),
    !b.length && c.rows(0).length ? ansLine('GP เทียบเป้า', 'ไม่มีสาขาเกินเป้า', '', 'good', '#gpbTable') : null,
    !b.length && c.rows(0).length ? c.rows(0).filter(r => _ansN(r, 2) != null).sort((x, y) => _ansN(x, 2) - _ansN(y, 2)).slice(0, 1)
      .map(r => ansLine('GP % ต่ำสุด', `${r.title} ${_ansF(r, 2)}`, `GP ${_ansB(r, 1)}`, 'warn', '#gpbTable'))[0] : null,
    t && ansLine(getBranch() ? 'GP ของสาขา' : 'GP ทั้งเครือ', `${_ansB(t, 1)} · ${_ansF(t, 2)}`, '', '', '#gpbTable')];
};
function phAnswerGo(i) {
  const pg = phCurPage(), panel = pg && document.getElementById('tab-' + pg.tab), strip = pg && document.querySelector(`.ph-ans[data-tab="${pg.tab}"]`);
  const l = strip && strip._lines && strip._lines[i]; if (!l || !l.go) return;
  if (l.go.charAt(0) === '@') { switchTab(l.go.slice(1)); return; }        // '@tab' = the page that answers the line in full
  let t = panel.querySelector(l.go); if (!t) return;
  const src = t.closest('.ph-cl-src');                                     // a table shown as cards: its cards
  if (src && src.previousElementSibling && src.previousElementSibling.matches('.ph-cl')) t = src.previousElementSibling;
  try { t.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { /* off screen */ }
}

// ── หน้าที่เปิดล่าสุด (ฉัน): the last three pages opened on this device ──
const _PH_RECENT_KEY = 'oshinei_ph_recent';
function _phRecentGet() { try { const a = JSON.parse(localStorage.getItem(_PH_RECENT_KEY) || '[]'); return Array.isArray(a) ? a : []; } catch (e) { return []; } }
function _phPageLabel(tab, pane) {
  const p = DEPARTMENTS.flatMap(d => d.pages).find(x => x.tab === tab && (x.pane || '') === (pane || ''));
  if (p) return { label: p.label, icon: p.icon };
  const t = document.getElementById('pageTitle');
  return { label: (t && t.textContent.trim()) || tab, icon: 'doc' };
}
function phRecentAdd(pg) {
  if (!pg || pg.tab === 'launcher') return;
  const k = pg.tab + '|' + (pg.pane || '');
  const list = _phRecentGet().filter(x => x && x.k !== k);
  const meta = _phPageLabel(pg.tab, pg.pane);
  list.unshift({ k, tab: pg.tab, pane: pg.pane || '', label: meta.label, icon: meta.icon });
  try { localStorage.setItem(_PH_RECENT_KEY, JSON.stringify(list.slice(0, 3))); } catch (e) { /* memory only */ }
}
function _phRecentHtml() {
  const rows = _phRecentGet().filter(x => x && x.tab && _phPageOk({ tab: x.tab, pane: x.pane || undefined })).slice(0, 3);
  return { n: rows.length, html: rows.map((x, i) => `<a href="#" class="ph-bigtile" data-nb="${_navPageSpec({ tab: x.tab, pane: x.pane })}" onclick="phRecentGo(${i});return false">`
    + `<span class="ph-badge" style="--dk:var(--accent);--dt:var(--accent-soft)">${_deptSvg(x.icon || 'doc', 26)}</span><span class="ph-bigtile-n">${esc(x.label)}</span></a>`).join('') };
}
function phRecentGo(i) {
  const x = _phRecentGet().filter(r => r && r.tab && _phPageOk({ tab: r.tab, pane: r.pane || undefined }))[i]; if (!x) return;
  _deptCur = null; _deptFocus = null;
  phOpenPage(() => { if (x.pane) supplyGo({ tab: x.tab, pane: x.pane }); else switchTab(x.tab); });
}

// ── Service worker (sw.js): the shell and the summary's last numbers survive a lost
// connection, and the morning brief can arrive as a notification. Registered for
// everyone — online nothing changes (network-first). The worker tells the page when it
// served a stale copy; #msOffline then says so with the time the copy was stored. ──
let _msOfflineAt = '';
function _msOfflineShow(stampIso) {
  const el = document.getElementById('msOffline'); if (!el) return;
  const t = stampIso ? new Date(stampIso) : null;
  const when = t && !isNaN(t) ? ` · ข้อมูลเมื่อ ${msClock(t.getTime())}` : '';
  el.textContent = `ออฟไลน์${when} · แสดงชุดล่าสุดที่โหลดไว้`;
  el.style.display = '';
}
function _msOfflineHide() { const el = document.getElementById('msOffline'); if (el) el.style.display = 'none'; }
if ('serviceWorker' in navigator && /^https?:$/.test(location.protocol)) {
  navigator.serviceWorker.register('/sw.js').catch(e => console.warn('sw:', e));
  navigator.serviceWorker.addEventListener('message', e => {
    const d = e.data || {};
    if (d.type === 'offline-fallback') { _msOfflineAt = d.cachedAt || _msOfflineAt; _msOfflineShow(_msOfflineAt); }
    if (d.type === 'notification-open') {
      try {
        const fm = fmsgParseLink(d.url || '');
        if (fm) { _fmsgPending = fm; fmsgOpenPending(); }     // PR-F4: a franchise message: the inbox (head office) or ร้านของฉัน (the owner)
        else if (String(d.url || '').includes('go=supply')) { if (phOn()) phOpenPage(() => supplyDeepLink(d.url)); else if (_desktopBooted) supplyDeepLink(d.url); else _supplyPendingLink = d.url; }   // a supply reminder (batch 3): that page, not the owner's summary
        else if (hasModule('report.management')) { _msPendingLink = d.url || ''; showMobileSummary(); }
        else if (phOn()) phShowHome(); else switchTab(tabAllowed('home') ? 'home' : firstAllowedTab());   // the owner's alerts go to everyone with sales.overview; the summary is management's
      } catch (err) {}
    }
  });
}
window.addEventListener('offline', () => _msOfflineShow(_msOfflineAt));
window.addEventListener('online', () => { _msOfflineHide(); _msOfflineAt = ''; if (_phoneFirst()) { try { msRefresh(); } catch (e) {} } });
if (navigator.onLine === false) setTimeout(() => _msOfflineShow(''), 0);

// ── Deep link: a notification opens "/?go=summary|branches|branch&b=<name>". Read once at
// load (the query is then dropped from the address bar) or handed over by sw.js when the
// app is already open; applied when the summary has data (loadMobileSummary). ──
let _msPendingLink = '';
(function () {
  try {
    const q = new URLSearchParams(location.search);
    if (q.get('go')) {
      if (q.get('go') === 'supply') _supplyPendingLink = location.search;     // supply reminders (batch 3) open a supply page
      else if (q.get('go') === 'fmsg' || (q.get('go') === 'franchise' && q.get('t'))) _fmsgPending = fmsgParseLink(location.search);   // PR-F4
      else _msPendingLink = location.search;
      history.replaceState(null, '', location.pathname + location.hash);
    }
  } catch (e) {}
})();
function msDeepLink(link) {
  let q; try { q = new URLSearchParams(String(link).includes('?') ? String(link).split('?')[1] : String(link)); } catch (e) { return; }
  const go = q.get('go'), b = q.get('b');
  if (go === 'branch' && b) { msSwitchView('branches'); msOpenBranch(b); }
  else if (go === 'branches') msSwitchView('branches');
  else if (go === 'discounts') { if (document.getElementById('msnav-discounts')?.style.display !== 'none') msSwitchView('discounts'); else msSwitchView('branches'); }
  else if (go === 'summary') {
    if (phOn() && _phKind() === 'centre') { phOpenDetail('yesterday'); return; }     // phone B: the morning brief opens the day's detail
    msSwitchView('summary');
    setTimeout(() => document.getElementById('msYesterdayWrap')?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 400);
  }
}

// ── Notifications: the morning brief on this phone at 08:30 (routers/push_api.py). The
// card shows only where push exists (an installed app on iOS, any Chrome on Android)
// and the server has its VAPID key; เปิด asks permission, subscribes and stores the
// device; ปิด removes it; ทดสอบ asks the server for one test notification. ──
let _msVapid = null;
function _msPushSupported() { return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window; }
function _msB64ToU8(s) { const pad = '='.repeat((4 - (s.length % 4)) % 4); const raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/')); const out = new Uint8Array(raw.length); for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i); return out; }
async function msPushVapid() {       // the server's public key ('' = push is not set up there)
  try {
    if (_msVapid === null) { const j = await api(`${API}/api/push/vapid-public`); _msVapid = (j && j.configured && j.key) ? j.key : ''; }
  } catch (e) { _msVapid = ''; }
  return _msVapid;
}
async function msPushInit() {
  const card = document.getElementById('msPush'); if (!card) return;
  if (!_msPushSupported()) { card.style.display = 'none'; return; }
  await msPushVapid();
  if (!_msVapid) { card.style.display = 'none'; return; }
  // "ไว้ก่อน" keeps the card away for 30 days; once this device is subscribed the card
  // stays out of the way too (a small ตั้งค่าการแจ้งเตือน link at the bottom brings it back)
  let sub = null; try { const reg = await navigator.serviceWorker.ready; sub = await reg.pushManager.getSubscription(); } catch (e) {}
  let snoozed = false; try { snoozed = Date.now() < (+localStorage.getItem('oshinei_push_snooze') || 0); } catch (e) {}
  const reopen = document.getElementById('msPushReopen');
  if (sub && !_msPushOpen) { card.style.display = 'none'; if (reopen) reopen.style.display = ''; return; }
  // 2026-10-20: blocked in this browser's settings - the card cannot do anything about it, so it does not sit at the top;
  // the bottom link opens it on purpose, with the steps to unblock (msPushRender)
  if (!sub && Notification.permission === 'denied' && !_msPushOpen) { card.style.display = 'none'; if (reopen) reopen.style.display = ''; _msPushWatchPermission(); return; }
  if (!sub && snoozed) { card.style.display = 'none'; if (reopen) reopen.style.display = ''; return; }
  card.style.display = '';
  if (reopen) reopen.style.display = 'none';
  await msPushRender();
}
let _msPushOpen = false;     // the card was opened on purpose (just enabled, or via the bottom link)
function msPushLater() {
  try { localStorage.setItem('oshinei_push_snooze', String(Date.now() + 30 * 86400000)); } catch (e) {}
  const card = document.getElementById('msPush'); if (card) card.style.display = 'none';
  const reopen = document.getElementById('msPushReopen'); if (reopen) reopen.style.display = '';
}
function msPushHide() {      // subscribed: tuck the card away, keep the bottom link
  _msPushOpen = false;
  const card = document.getElementById('msPush'); if (card) card.style.display = 'none';
  const reopen = document.getElementById('msPushReopen'); if (reopen) reopen.style.display = '';
}
function msPushReopen() {
  _msPushOpen = true;
  try { localStorage.removeItem('oshinei_push_snooze'); } catch (e) {}
  if (phOn()) { phOpenMe(); return; }                 // PR-D3: the card lives in ฉัน on the phone shell
  msPushInit();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
// `p` is the id prefix of the card: 'msPush' = the owner's phone summary, 'shmPush' = หน้างาน of the supply module (batch 3, 2.1), where
// branch and centre logins turn on the reminders of their own work (kind "supply"). Same functions, same server calls.
function _pushSupplyCard(p) { return p === 'shmPush'; }
async function msPushRender(note, p) {
  p = p || 'msPush';
  const st = document.getElementById(p + 'State'), on = document.getElementById(p + 'On'), off = document.getElementById(p + 'Off'), test = document.getElementById(p + 'Test');
  if (!st) return;
  let sub = null;
  try { const reg = await navigator.serviceWorker.ready; sub = await reg.pushManager.getSubscription(); } catch (e) {}
  // the browser may hold a subscription the server no longer has (a failed POST, a dropped
  // row, a DB swap before the table was preserved): store it again, quietly
  if (sub) {
    try {
      const st = await api(`${API}/api/push/status`);
      if (st && st.my_devices === 0) {
        await fetch(`${API}/api/push/subscribe`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...sub.toJSON(), ua: navigator.userAgent.slice(0, 200) }) });
      }
    } catch (e) {}
  }
  const denied = Notification.permission === 'denied';
  const sup = _pushSupplyCard(p);
  const frc = p === 'frcPush';        // PR-F2: ร้านของฉัน, the franchise owner's own morning summary (kind "franchise")
  if (sub) st.textContent = note || (frc ? 'เปิดอยู่ · สรุปร้านของฉันทุกเช้า 08:30' : sup ? 'เปิดอยู่ · งานวันนี้ และเตือนก่อนเวลาส่งสุดท้าย 13:15' : 'เปิดอยู่ · สรุปเมื่อวานทุกเช้า 08:30');
  else if (denied) { st.innerHTML = _msPushBlockedHelp(p); _msPushWatchPermission(); }
  else st.textContent = note || (frc ? 'ยังไม่เปิด · สรุปร้านของฉันทุกเช้า 08:30' : sup ? 'ยังไม่เปิด · งานวันนี้ และเตือนก่อนเวลาส่งสุดท้าย 13:15' : 'ยังไม่เปิด · สรุปเมื่อวานทุกเช้า 08:30');
  if (on) on.style.display = sub || denied ? 'none' : '';
  if (off) off.style.display = sub ? '' : 'none';
  if (test) test.style.display = sub ? '' : 'none';
  const later = document.getElementById(p + 'Later');
  if (later) { later.textContent = sub ? 'ซ่อน' : 'ไว้ก่อน'; later.setAttribute('onclick', sub ? 'msPushHide()' : 'msPushLater()'); }
  msPushKinds(sub, p);
}
// Blocked (Notification.permission 'denied'): only the person can lift it, in the browser's own settings. Say where, for
// this kind of device, and offer ตรวจอีกครั้ง; the permission watcher also re-renders by itself when it changes.
function _msPushBlockedHelp(p) {
  const ua = navigator.userAgent || '';
  const how = /iPhone|iPad|iPod/.test(ua)
    ? 'เปิด ตั้งค่า › การแจ้งเตือน › Oshinei แล้วเปิด “อนุญาตการแจ้งเตือน” (ต้องเปิดแอปจากไอคอนบนหน้าจอโฮม)'
    : /Android/.test(ua)
      ? 'แตะไอคอนแม่กุญแจหรือ ⓘ ข้างที่อยู่เว็บ › สิทธิ์ › การแจ้งเตือน › อนุญาต'
      : 'คลิกไอคอนแม่กุญแจข้างที่อยู่เว็บ › การแจ้งเตือน › อนุญาต';
  return `การแจ้งเตือนถูกปิดไว้ในการตั้งค่าของเครื่องนี้<br><b style="color:var(--text-primary)">วิธีเปิด:</b> ${how}`
    + `<br><button type="button" onclick="msPushRecheck('${p}')" style="margin-top:8px;min-height:40px;padding:8px 14px;font-size:13px;font-weight:600;border-radius:var(--r-md);border:1px solid var(--border,#e2e8f0);background:var(--bg-card,#fff);color:var(--text-primary,#1e293b);cursor:pointer">ตรวจอีกครั้ง</button>`;
}
function msPushRecheck(p) {
  if ((p || 'msPush') === 'msPush') { _msPushOpen = true; msPushInit(); }
  else msPushRender(undefined, p);
}
let _msPermWatched = false;
function _msPushWatchPermission() {        // re-render both cards when the person changes the permission
  if (_msPermWatched || !(navigator.permissions && navigator.permissions.query)) return;
  _msPermWatched = true;
  navigator.permissions.query({ name: 'notifications' }).then(st => {
    st.onchange = () => {
      if (document.getElementById('msPush')) { _msPushOpen = true; msPushInit(); }
      if (document.getElementById('shmPushState')) msPushRender(undefined, 'shmPush');
      if (document.getElementById('frcPushState')) msPushRender(undefined, 'frcPush');
    };
  }).catch(() => { _msPermWatched = false; });
}
// one checkbox per kind of notification, saved on the server per device (push_subscription.off)
async function msPushKinds(sub, p) {
  p = p || 'msPush';
  const box = document.getElementById(p + 'Kinds'); if (!box) return;
  if (!sub) { box.style.display = 'none'; box.innerHTML = ''; return; }
  let j;
  try {
    const r = await fetch(`${API}/api/push/prefs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ endpoint: sub.endpoint }) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    j = await r.json();
  } catch (e) { box.style.display = 'none'; return; }
  box.innerHTML = '<div style="font-size:12px;color:var(--text-muted,#64748b);margin:10px 0 0">ชนิดที่ต้องการรับ</div>' +
    j.kinds.map(k => `<label style="display:flex;align-items:center;gap:10px;min-height:44px;font-size:13px;color:var(--text-primary,#1e293b)"><input type="checkbox" data-kind="${esc(k.id)}" ${(j.off || []).includes(k.id) ? '' : 'checked'} onchange="msPushKindsSave('${p}')" style="width:20px;height:20px;accent-color:var(--accent)">${esc(k.label)}</label>`).join('');
  box.style.display = '';
}
async function msPushKindsSave(p) {
  p = p || 'msPush';
  const box = document.getElementById(p + 'Kinds'), st = document.getElementById(p + 'State');
  if (!box) return;
  const off = [...box.querySelectorAll('input[data-kind]')].filter(i => !i.checked).map(i => i.dataset.kind);
  try {
    const reg = await navigator.serviceWorker.ready; const sub = await reg.pushManager.getSubscription();
    if (!sub) return;
    const r = await fetch(`${API}/api/push/prefs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ endpoint: sub.endpoint, off }) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    if (st) st.textContent = off.length ? `บันทึกแล้ว · ปิดอยู่ ${off.length} ชนิด` : 'บันทึกแล้ว · รับครบทุกชนิด';
  } catch (e) { if (st) st.textContent = 'บันทึกไม่สำเร็จ: ' + (e && e.message ? e.message : e); }
}
async function msPushEnable(p) {
  p = p || 'msPush';
  const st = document.getElementById(p + 'State');
  try {
    if (_msVapid === null) await msPushVapid();
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') { await msPushRender(perm === 'denied' ? '' : 'ยังไม่ได้อนุญาต', p); return; }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: _msB64ToU8(_msVapid) });
    const r = await fetch(`${API}/api/push/subscribe`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...sub.toJSON(), ua: navigator.userAgent.slice(0, 200) }) });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      try { await sub.unsubscribe(); } catch (e2) {}   // keep the browser and the server in step
      throw new Error(d.detail || ('HTTP ' + r.status));
    }
    _msPushOpen = true;      // stay visible so ส่งทดสอบ can be tried; ซ่อน tucks it away
    await msPushRender(undefined, p);
  } catch (e) { if (st) st.textContent = 'เปิดไม่สำเร็จ: ' + (e && e.message ? e.message : e); }
}
async function msPushDisable(p) {
  try {
    const reg = await navigator.serviceWorker.ready; const sub = await reg.pushManager.getSubscription();
    if (sub) {
      try { await fetch(`${API}/api/push/unsubscribe`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ endpoint: sub.endpoint }) }); } catch (e) {}
      await sub.unsubscribe();
    }
  } catch (e) {}
  await msPushRender('ปิดแล้ว', p);
}
async function msPushTest(p) {
  p = p || 'msPush';
  const st = document.getElementById(p + 'State');
  if (st) st.textContent = 'กำลังส่งทดสอบ…';
  try {
    const r = await fetch(`${API}/api/push/test`, { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
    const codes = (d.statuses || []).join(', ');
    await msPushRender(d.sent ? `ส่งทดสอบแล้ว · ดูการแจ้งเตือนบนเครื่อง (push service ตอบ ${codes})`
                             : `ส่งไม่ถึง (${d.failed || 0} ล้มเหลว, ${d.gone || 0} หมดอายุ · push service ตอบ ${codes || 'ไม่มี'})`, p);
  } catch (e) { if (st) st.textContent = 'ส่งทดสอบไม่สำเร็จ: ' + (e && e.message ? e.message : e); }
}

// ── Install. Chrome / Edge / Samsung fire beforeinstallprompt: keep the event and offer
// one ติดตั้ง button. iOS has no such event: Safari gets a one-line hint instead
// (แชร์ → เพิ่มไปยังหน้าจอโฮม). Shown on the phone summary only, never inside the
// installed app, and × keeps it away for 30 days (a completed install for a year). ──
let _installEvt = null;
function _msStandalone() { try { return window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true; } catch (e) { return false; } }
function _msInstallSnoozed() { try { return Date.now() < (+localStorage.getItem('oshinei_install_snooze') || 0); } catch (e) { return false; } }
function msShowInstall(mode) {
  const w = document.getElementById('msInstall'); if (!w || _msStandalone() || _msInstallSnoozed()) return;
  const btn = document.getElementById('msInstallBtn'), hint = document.getElementById('msInstallHint');
  if (mode === 'ios') { if (btn) btn.style.display = 'none'; if (hint) hint.textContent = 'กดปุ่มแชร์ของ Safari แล้วเลือก "เพิ่มไปยังหน้าจอโฮม"'; }
  else if (btn) btn.style.display = '';
  w.style.display = '';
}
async function msInstallApp() {
  if (!_installEvt) return;
  try { _installEvt.prompt(); const r = await _installEvt.userChoice; if (r && r.outcome === 'accepted') msInstallDismiss(365); } catch (e) {}
  _installEvt = null;
}
function msInstallDismiss(days) {
  try { localStorage.setItem('oshinei_install_snooze', String(Date.now() + (days || 30) * 86400000)); } catch (e) {}
  const w = document.getElementById('msInstall'); if (w) w.style.display = 'none';
}
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); _installEvt = e; msShowInstall('prompt'); try { shmInstallRefresh(); } catch (err) { /* หน้างาน (branch) is not loaded yet */ } });
window.addEventListener('appinstalled', () => { msInstallDismiss(365); try { shmInstallRefresh(); } catch (err) { /* idem */ } });
(function () {
  const ua = navigator.userAgent || '';
  if (/iPhone|iPad|iPod/.test(ua) && /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua)) msShowInstall('ios');
})();

// ── The top bar folds on a phone. Its controls (branch · period · search · filters ·
// presets · tools · user · uploads) are six or seven rows of chrome before the first
// number on a 360 px screen; the fold button at the right of the title row hides them
// until they are wanted. Remembered per device (oshinei_tb_fold), folded by default.
// The CSS applies body.tb-folded at ≤700 px only (index.html, MOBILE), so the class is
// inert on a desktop; the branch login keeps its bare bar instead (#314).
function applyTopbarFold(folded) {
  document.body.classList.toggle('tb-folded', !!folded);
  const b = document.getElementById('tbFoldBtn');
  if (!b) return;
  b.setAttribute('aria-expanded', folded ? 'false' : 'true');
  b.title = folded ? 'แสดงตัวกรองและเครื่องมือ' : 'ซ่อนตัวกรองและเครื่องมือ';
  const c = document.getElementById('tbFoldChev');
  if (c) c.textContent = folded ? '▾' : '▴';
}
function toggleTopbarFold() {
  const folded = !document.body.classList.contains('tb-folded');
  try { localStorage.setItem('oshinei_tb_fold', folded ? '1' : '0'); } catch (e) {}
  applyTopbarFold(folded);
}
function initTopbarFold() {
  let v = null;
  try { v = localStorage.getItem('oshinei_tb_fold'); } catch (e) {}
  applyTopbarFold(v === null ? true : v === '1');
}

// ── shared state + helpers ──
let _msView = 'summary', _msRpt = null, _msBranchesFilled = false, _msMonthsFilled = false,
    _msSalesGran = 'month', _msBranchSort = 'chg';    // DV6: the branch list opens on the baht against last month
function msBranch() { return document.getElementById('msBranch')?.value || ''; }
function msPchip(v, goodUp = true, unit = '%') {
  if (v == null) return '<span class="ms-chip" style="background:#f1f5f9;color:#5f6c80">—</span>';
  const good = goodUp ? v >= 0 : v <= 0;
  const c = v === 0 ? '#5f6c80' : good ? '#047857' : '#b91c1c';   // on a fixed light chip
  const bg = v === 0 ? '#f1f5f9' : good ? '#dcfce7' : '#fee2e2';
  return `<span class="ms-chip" style="background:${bg};color:${c}">${v > 0 ? '▲' : v < 0 ? '▼' : ''} ${Math.abs(v)}${unit}</span>`;
}
// avgW (0-100, optional): a tick at the list's average, so above/below reads at a glance.
function msBar(w, col, avgW) {
  const tick = avgW == null ? '' : `<span style="position:absolute;top:0;bottom:0;left:${Math.max(0, Math.min(99, avgW))}%;width:2px;background:var(--text-primary,#1e293b);opacity:.55"></span>`;
  return `<div style="flex:1;position:relative;background:var(--bg-subtle);border-radius:3px;overflow:hidden"><div style="width:${w}%;min-width:2px;height:8px;background:${col}"></div>${tick}</div>`;
}

// ── at-a-glance pieces for the phone. The dashboard's charts get the same from
// the glance layer in js/01-core.js; the sentences come from the same builders
// (glanceTrendSentence / glanceRangeSentence), so both say things one way. ──
const _MS_TH_MON = ['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
const _MS_TH_DOW = ['อา.','จ.','อ.','พ.','พฤ.','ศ.','ส.'];
const _msYmd = iso => String(iso || '').slice(0, 10).split('-').map(Number);
function msPeriodLabel(iso, gran) {
  const [y, m, d] = _msYmd(iso);
  return gran === 'month' ? `${_MS_TH_MON[m - 1]} ${String(y + 543).slice(2)}` : `${d} ${_MS_TH_MON[m - 1]}`;
}
const msDow = iso => { const [y, m, d] = _msYmd(iso); return new Date(y, m - 1, d).getDay(); };
// Index (oldest→newest) of the period still in progress, plus "12/30 วัน".
function msPartial(trend, gran) {
  const last = trend.length - 1;
  if (last < 0) return { i: -1, note: '' };
  const [y, m, d] = _msYmd(trend[last].start);
  // PR-PP2: /api/sales/daily marks the period that is to date through the last closed day (`partial`, `end`)
  if (trend[last].partial !== undefined) {
    if (!trend[last].partial) return { i: -1, note: '' };
    const e = _msYmd(trend[last].end), n = Math.round((Date.UTC(e[0], e[1] - 1, e[2]) - Date.UTC(y, m - 1, d)) / 864e5) + 1;
    return { i: last, note: gran === 'month' ? `${e[2]}/${new Date(y, m, 0).getDate()} วัน` : gran === 'week' ? `${n}/7 วัน` : '' };
  }
  if (gran === 'month') {
    const ref = _glanceLastDate(), dim = new Date(y, m, 0).getDate();
    return (y === ref.y && m === ref.m && ref.d < dim) ? { i: last, note: `${ref.d}/${dim} วัน` } : { i: -1, note: '' };
  }
  const t = new Date(Date.now() + 7 * 3600e3);   // a day still trading = today, Bangkok
  if (gran === 'week') {
    const s0 = Date.UTC(y, m - 1, d), tt = Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate());
    return (tt >= s0 && tt < s0 + 7 * 86400e3) ? { i: last, note: `${Math.round((tt - s0) / 86400e3) + 1}/7 วัน` } : { i: -1, note: '' };
  }
  return (y === t.getUTCFullYear() && m === t.getUTCMonth() + 1 && d === t.getUTCDate()) ? { i: last, note: '' } : { i: -1, note: '' };
}
const msGlanceLine = html => html ? `<p style="font-size:13px;line-height:1.45;color:var(--text-secondary,#475569);margin:2px 0 8px">${html}</p>` : '';

// A small column chart in SVG (no Chart.js on the phone): oldest→newest, theme B1 (VZ2) like the dashboard's bars — gradient
// columns with rounded tops, above the average teal→indigo, below it lilac, the latest complete period gold with a dark edge,
// the period in progress hollow + dashed, a dot under each weekend, a dashed average line with its value, labels on the latest
// and the peak. Under 4 finished periods it keeps the plain accent columns.
function msColumns(vals, o) {
  const n = vals.length;
  if (n < 2) return '';
  // o.under (optional): one {t, c} per column printed under its label (the month-by-month view
  // prints the change on the previous month); o.allLabels labels every column, not just the ends
  const under = Array.isArray(o.under) ? o.under : null;
  const P = o.partial == null ? -1 : o.partial;
  const b1 = glanceB1Kinds(vals, { partial: P, weekend: o.weekend });
  const wkDots = !!(b1 && (o.weekend || []).some(Boolean));
  const W = 320, H = o.height || 100, top = 16, bottom = (under ? 31 : 14) + (wkDots ? 6 : 0), plotH = H - top - bottom;
  const max = Math.max(1, ...vals.filter(v => v != null));
  const step = W / n, bw = Math.max(3, Math.min(22, step * 0.64));
  const yOf = v => top + plotH * (1 - Math.max(0, v) / max);
  const comp = vals.map((v, i) => i).filter(i => vals[i] != null && i !== P);
  const last = comp.length ? comp[comp.length - 1] : -1;
  const hi = comp.reduce((b, i) => (b < 0 || vals[i] > vals[b]) ? i : b, -1);
  const avg = comp.length >= 3 ? comp.reduce((a, i) => a + vals[i], 0) / comp.length : null;
  const cx = i => i * step + step / 2;
  const placed = [];
  // U3 (owner 2026-10-22, ร้านของฉัน 12 เดือน: "เดือนนี้" sat on the value label): a Thai line is about 1.6 x the font size tall (marks above
  // and below), so two labels closer than that overlap; a label that meets one moves up a line above it while it stays inside the chart,
  // else it is left out
  const LINE = 1.6;
  const HALO = 'paint-order:stroke;stroke:var(--bg-card,#fff);stroke-width:3px;stroke-linejoin:round';
  const txt = (t, x, y, size, weight, color, check) => {
    const w = t.length * size * 0.56;
    x = Math.max(w / 2 + 1, Math.min(W - w / 2 - 1, x));
    if (check) {
      const hit = yy => placed.find(p => Math.abs(p.x - x) < (p.w + w) / 2 + 3 && Math.abs(p.y - yy) < size * LINE);
      const h = hit(y);
      if (h) {
        const up = h.y - size * LINE;
        if (up < size || hit(up)) return '';
        y = up;
      }
      placed.push({ x, y, w });
    }
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" font-size="${size}" font-weight="${weight}" style="fill:${color};${HALO}">${esc(t)}</text>`;
  };
  const uid = 'mc' + Math.abs(vals.reduce((a, v, i) => (a * 31 + Math.round(v || 0) + i) | 0, 7)).toString(36);
  // a column with rounded tips (the base is flat), as a path so the gradient follows it
  const colPath = (x, y, w, h) => { const r = Math.min(4, w / 2, h); return `M${x.toFixed(1)},${(y + h).toFixed(1)}V${(y + r).toFixed(1)}a${r},${r} 0 0 1 ${r},-${r}h${(w - 2 * r).toFixed(1)}a${r},${r} 0 0 1 ${r},${r}V${(y + h).toFixed(1)}z`; };
  const FB = { a1: '#0c8f81', a2: '#3a2fb5', aw1: '#0f9d8e', aw2: '#5148d4', b1: '#8f78df', b2: '#7e62d2', bw1: '#9a84e6', bw2: '#8468d8', t1: '#f0a500', t2: '#b86e00' };
  let g = '';
  if (b1) {
    g += '<defs>' + ['a', 'aw', 'b', 'bw', 't'].map(k => `<linearGradient id="${uid}${k}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--ch-${k}1,${FB[k + '1']})"/><stop offset="1" style="stop-color:var(--ch-${k}2,${FB[k + '2']})"/></linearGradient>`).join('') + '</defs>';
  } else {
    (o.weekend || []).forEach((w, i) => { if (w) g += `<rect x="${(i * step).toFixed(1)}" y="${top - 4}" width="${step.toFixed(1)}" height="${plotH + 4}" style="fill:var(--bg-subtle,#f1f5f9)"/>`; });
  }
  if (avg != null) {
    const ya = yOf(avg).toFixed(1);
    g += `<line x1="0" x2="${W}" y1="${ya}" y2="${ya}" stroke-width="1.4" stroke-dasharray="5 3" style="stroke:var(--ch-avg,#475569)"/>`;
  }
  vals.forEach((v, i) => {
    if (v == null) return;
    const x = cx(i) - bw / 2, y = yOf(v), h = Math.max(1, top + plotH - y);
    if (b1) {
      const kd = b1.kinds[i], key = kd.tone === 'now' ? 't' : (kd.tone === 'hi' ? 'a' : 'b') + (kd.wk ? 'w' : '');
      if (i === P) g += `<path d="${colPath(x, y, bw, h)}" stroke-width="1.4" stroke-dasharray="3 2" style="fill:none;stroke:var(--ch-${key}2,${FB[key + '2']})"/>`;
      else g += `<path d="${colPath(x, y, bw, h)}" fill="url(#${uid}${key})"${kd.tone === 'now' ? ' stroke-width="1.6" style="stroke:var(--ch-tstroke,#7a4a00)"' : ''}/>`;
      if (kd.wk) g += `<circle cx="${cx(i).toFixed(1)}" cy="${(top + plotH + 5).toFixed(1)}" r="2" style="fill:var(--text-muted,#94a3b8)"/>`;
      return;
    }
    if (i === P) g += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" stroke-width="1.2" stroke-dasharray="3 2" style="fill:none;stroke:var(--group, var(--accent,#1f3a6b))"/>`;
    else g += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="3" style="fill:var(--group, var(--accent,#1f3a6b));opacity:${i === last ? 1 : 0.38}"/>`;
  });
  const lab = (i, t) => txt(t ?? o.fmt(vals[i]), cx(i), yOf(vals[i]) - 4, 12, 700, b1 && i === last ? 'var(--gold-i,#7a4500)' : 'var(--text-primary,#1e293b)', true);
  // priority: the latest complete value, then the period in progress (just its
  // tag - the figure is in the list/tile), then the peak
  if (last >= 0) g += lab(last);
  if (P >= 0 && vals[P] != null) g += lab(P, o.grain === 'day' ? 'วันนี้' : 'เดือนนี้');
  if (hi >= 0 && hi !== last) g += lab(hi);
  if (avg != null) {   // 2026-10-29: the average label used to sit on the first column's value - it now yields: above the line, else below it
    const t = `เฉลี่ย ${o.fmt(avg)}`, w = t.length * 12 * 0.56, ax = w / 2 + 2, ya = yOf(avg);
    const free = yy => !placed.find(p => Math.abs(p.x - ax) < (p.w + w) / 2 + 3 && Math.abs(p.y - yy) < 12 * LINE);
    const yy = free(ya - 3) ? ya - 3 : free(ya + 13) ? ya + 13 : null;
    if (yy != null) { placed.push({ x: ax, y: yy, w }); g += `<text x="2" y="${yy.toFixed(1)}" font-size="12" font-weight="700" style="fill:var(--text-secondary,#475569);${HALO}">${esc(t)}</text>`; }
  }
  if (o.labels) {
    const ly = under ? H - 17 : H - 2;
    if (o.allLabels) o.labels.forEach((t, i) => { g += txt(t, cx(i), ly, 12, i === last ? 700 : 600, i === last ? 'var(--text-primary,#1e293b)' : 'var(--text-muted,#94a3b8)'); });
    else {
      g += txt(o.labels[0], cx(0), ly, 12, 600, 'var(--text-muted,#94a3b8)');
      if (b1 && n > 4) g += txt(o.labels[Math.floor((n - 1) / 2)], cx(Math.floor((n - 1) / 2)), ly, 12, 600, 'var(--text-muted,#94a3b8)');   // first / middle / last
      g += txt(o.labels[n - 1], cx(n - 1), ly, 12, 600, 'var(--text-muted,#94a3b8)');
    }
  }
  if (under) under.forEach((u, i) => { if (u && u.t) g += txt(u.t, cx(i), H - 3, 12, 600, u.c || 'var(--text-muted,#94a3b8)'); });
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;font-family:inherit" role="img" aria-label="${esc(o.aria || '')}">${g}</svg>`;
}
// Trend block = sentence + columns, for a trend array oldest→newest from /api/sales/daily.
function msTrendBlock(trend, gran, height) {
  const vals = trend.map(x => x.revenue ?? null);
  const labels = trend.map(x => msPeriodLabel(x.start, gran));
  const part = msPartial(trend, gran);
  const sentence = glanceTrendSentence(labels, vals, { partial: part.i, note: part.note, unit: gran === 'month' ? 'เดือน' : gran === 'week' ? 'สัปดาห์' : 'วัน', fmt });
  return msGlanceLine(sentence) + msColumns(vals, {
    partial: part.i, labels, fmt, grain: gran, height,
    weekend: gran === 'day' ? trend.map(x => [0, 6].includes(msDow(x.start))) : null,
    aria: 'รายได้ ' + (gran === 'month' ? 'รายเดือน' : gran === 'week' ? 'รายสัปดาห์' : 'รายวัน'),
  });
}

// phone views → group colours (theme-groups.css): the section markers follow the view
const _MS_GROUP = { summary: 'home', sales: 'sales', branches: 'buffet', menu: 'menu', profit: 'profit', discounts: 'money', voids: 'money' };
const _MS_VIEWS = ['summary', 'sales', 'branches', 'menu', 'profit', 'discounts', 'voids'];
// a phone reader sees only what their role may open on the desktop (same module names as auth.py)
const _msMod = m => !(window.AUTH && window.AUTH.enabled) || (window.AUTH.modules || []).includes(m);
function msShowView(v) {
  document.body.dataset.group = _MS_GROUP[v] || 'home';
  _MS_VIEWS.forEach(x => {
    const el = document.getElementById('msv-' + x); if (el) el.style.display = x === v ? '' : 'none';
    document.getElementById('msnav-' + x)?.classList.toggle('active', x === v);
  });
}
function msSwitchView(v) {
  // phone B: on the phone shell a view is a detail screen with its own back (history); สรุป is the cards of หน้าแรก
  if (phOn() && _phKind() === 'centre') { if (v === 'summary') phDetailClose(); else phOpenDetail(v); return; }
  _msView = v;
  msShowView(v);
  window.scrollTo(0, 0);
  msRenderActive();
}
function msRenderActive() {
  if (!_msRpt) { loadMobileSummary(true); return; }
  if (_msView === 'summary')        msRenderSummary();
  else if (_msView === 'branches')  msRenderBranches();
  else if (_msView === 'sales')     { msLoadSales(); msRenderPeakPay(); }
  else if (_msView === 'menu')      msLoadMenu();
  else if (_msView === 'profit')    msRenderProfit();
  else if (_msView === 'discounts') msRenderDiscounts();
  else if (_msView === 'voids')     msLoadVoids();
}

// ── data: ONE management-report payload per (month, branch) drives the summary,
// branches, profit, discounts, peak/payment views and the branch sheet — the same
// numbers as the printed pack (owner request 2026-10-01: "see deeper on the phone").
// mtd=1 lets it describe the in-progress month, month-to-date and MTD-fair. ──
const _msRptCache = {};
let _msFetchedAt = 0;   // when the summary last came from the server (foreground refresh, 03-tabs-main.js)
function msClock(t) { const d = new Date(t); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
function msMonth() { return document.getElementById('msMonthSel')?.value || ''; }
function msMgtParams(branch, month) {
  const q = new URLSearchParams({ period: 'monthly', mtd: '1', lang: 'th' });
  if (month) q.set('month', month);
  if (branch) q.set('branch', branch);
  return q;
}
async function msFetchRpt(branch, month) {
  const key = (month || '') + '|' + (branch || '');
  if (_msRptCache[key]) return _msRptCache[key];
  const j = await api(`${API}/api/reports/management?${msMgtParams(branch, month)}`);
  if (j && j.available) { _msRptCache[key] = j; _msFetchedAt = Date.now(); }
  return j;
}
function msRefresh() {
  Object.keys(_msRptCache).forEach(k => delete _msRptCache[k]);
  Object.keys(_msBriefCache).forEach(k => delete _msBriefCache[k]); _msScorecard = null;
  Object.keys(_msMonthsCache).forEach(k => delete _msMonthsCache[k]);
  loadMobileSummary(true);
}
// a month-only report comes from the complete-month path unless the reader picked
// an older month, in which case the day/menu calls must be anchored on it too
function msPickedOlderMonth() {
  const j = _msRpt, first = ((j || {}).months || [])[0];
  return !!(j && j.selected_month && first && j.selected_month !== first.month);
}

// ── DV6 (owner 2026-10-25: "อย่าลืมให้ filter ได้ด้วย"): the summary follows the app's ONE period and branch. On opening it takes the
// branch of the top bar (#selBranch) and the month that holds the end of the top bar's period (gPeriodResolve); its own two pickers write
// back - a month picked here sets the top bar to that month, a branch sets #selBranch - so the pages opened next show the same scope.
// A pinned login has neither picker (stripBranchPickers). ──
let _msFromPick = false, _msShownMonth = null;
function _msEnsureOpt(sel, v, label) {
  if (v && ![...sel.options].some(o => o.value === v)) { const o = document.createElement('option'); o.value = v; o.textContent = label || v; sel.appendChild(o); }
}
function msPullGlobal() {
  const g = /** @type {HTMLSelectElement|HTMLInputElement|null} */ (document.getElementById('selBranch'));
  const mb = /** @type {HTMLSelectElement|null} */ (document.getElementById('msBranch'));
  if (g && mb && mb.tagName === 'SELECT') { _msEnsureOpt(mb, g.value || ''); mb.value = g.value || ''; }
  const ms = /** @type {HTMLSelectElement|null} */ (document.getElementById('msMonthSel'));
  const r = typeof gPeriodResolve === 'function' ? gPeriodResolve() : null;
  if (!ms || !r) return;
  const m = r.cur[1].slice(0, 7), open = !r.complete && r.cur[1] >= gPeriodAnchor();
  if ([...ms.options].some(o => o.value === m)) ms.value = m;
  else if (open) ms.value = '';                                 // the month in progress = the default "เดือนล่าสุด"
  else { _msEnsureOpt(ms, m, typeof fmtMonthTh === 'function' ? fmtMonthTh(m) : m); ms.value = m; }
}
function msPickChanged() {
  const g = /** @type {HTMLSelectElement|HTMLInputElement|null} */ (document.getElementById('selBranch'));
  const b = msBranch(), m = msMonth();
  if (g && g.tagName === 'SELECT' && document.getElementById('msBranch')) {
    _msEnsureOpt(/** @type {HTMLSelectElement} */ (g), b);
    if (g.value !== b) { g.value = b; if (typeof branchChipRender === 'function') branchChipRender(); }
  }
  // the month picked here becomes the top bar's period (to date when it is the month in progress; '' = เดือนล่าสุด)
  if (m !== _msShownMonth && typeof gPeriodSet === 'function') gPeriodSet({ kind: 'month', pick: m ? m + '-01' : '' }, true);
  if (typeof _tabLastKey === 'object') Object.keys(_tabLastKey).forEach(k => delete _tabLastKey[k]);
  _msFromPick = true;
  loadMobileSummary(true);
}

// ── entry point: fetch the month payload, then render the active view ──
async function loadMobileSummary(force) {
  if (!_msFromPick) { try { msPullGlobal(); } catch (e) { /* the top bar is not drawn yet */ } }
  _msFromPick = false;
  _msShownMonth = msMonth();
  const status = document.getElementById('msStatus');
  if (status) status.textContent = 'กำลังโหลด…';
  const navP = document.getElementById('msnav-profit'), navD = document.getElementById('msnav-discounts');
  if (navP) navP.style.display = (_msMod('cost.analysis') || _msMod('profit.pnl')) ? '' : 'none';
  if (navD) navD.style.display = _msMod('ops.discounts') ? '' : 'none';
  const br = msBranch(), mon = msMonth();
  let j;
  try { j = await msFetchRpt(br, mon); }
  catch (e) { if (status) status.textContent = 'โหลดไม่สำเร็จ — ลองรีเฟรช'; phOvRender(); return; }
  if (!j || !j.available) { if (status) status.textContent = 'ยังไม่มีข้อมูลการขาย'; phOvRender(); return; }
  if (br !== msBranch() || mon !== msMonth()) return;   // the reader moved on while this loaded
  _msRpt = j;
  if (!_msBranchesFilled && !br && (j.branches || []).length) {
    const sel = document.getElementById('msBranch');
    if (sel) {
      j.branches.map(b => b.branch).sort((a, b) => a.localeCompare(b, 'th'))
        .forEach(n => { const o = document.createElement('option'); o.value = n; o.textContent = n; sel.appendChild(o); });
      _msBranchesFilled = true;
    }
  }
  const msel = document.getElementById('msMonthSel');
  if (msel && !_msMonthsFilled && (j.months || []).length) {
    msel.innerHTML = j.months.map(m => `<option value="${esc(m.month)}">${esc(m.label)}</option>`).join('');
    msel.value = j.selected_month || j.months[0].month;
    _msMonthsFilled = true;
  }
  document.getElementById('msSubtitle').textContent = (br || 'ทุกสาขา') + ' · ' + (j.label || '');
  if (status) status.textContent = `เทียบ${j.comp_label || 'เดือนก่อน'} · ข้อมูลถึง ${j.latest_day ? fmtDateTh(j.latest_day) : ''}` + (_msFetchedAt ? ` · โหลดล่าสุด ${msClock(_msFetchedAt)}` : '');
  // phone B: on หน้าแรก the cards are drawn, and the summary's sections behind them (a card's detail opens at once); a detail screen draws its view
  const shell = phOn() && _phKind() === 'centre';
  if (!shell || _phView === 'summary') msRenderActive();
  else if (_msView === 'summary') { try { msRenderSummary(); } catch (e) { console.error('summary', e); } }
  if (shell) { phOvRender(); phTopSync(); }
  msLoadVoice();
  if (_msPendingLink) { const l = _msPendingLink; _msPendingLink = ''; msDeepLink(l); }
}

// ── the customer-voice queue on the phone (E4): what waits for an answer, the
// branch's own when the login is scoped, else the selected branch / the chain ──
async function msLoadVoice() {
  const wrap = document.getElementById('msVoiceWrap'), box = document.getElementById('msVoice');
  if (!wrap || !box) return;
  if (window.AUTH && window.AUTH.enabled && !(window.AUTH.modules || []).includes('customer.feedback')) { wrap.style.display = 'none'; _msVoiceN = 0; return; }
  const p = new URLSearchParams({ needs_reply: '1', sort: 'priority', limit: '5' });
  const br = (window.AUTH && window.AUTH.branch) || msBranch(); if (br) p.set('branch', br);
  let d;
  try { const r = await fetch(`${API}/api/feedback/items?${p}`); if (!r.ok) { wrap.style.display = 'none'; return; } d = await r.json(); }
  catch (e) { wrap.style.display = 'none'; return; }
  const items = d.items || [];
  wrap.style.display = items.length ? '' : 'none';   // nothing waiting = no section (owner 2026-10-29)
  if (_msVoiceN !== items.length) { _msVoiceN = items.length; phOvRender(); }   // phone B: the เสียงลูกค้า card of หน้าแรก
  document.getElementById('msVoiceCount').textContent = items.length ? `(${items.length}${items.length >= 5 ? '+' : ''})` : '';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  box.innerHTML = items.length ? items.map(r => `<div style="padding:8px 0;border-bottom:1px solid var(--border,#e2e8f0)">
      <div style="font-size:13px;color:var(--text-muted,#64748b)">${esc(r.source)} · <b style="color:var(--text-primary,#1e293b)">${esc(r.branch_name || '')}</b>${r.rating != null ? ` · ${r.rating}★` : ''} · ${r.age_days === 0 ? 'วันนี้' : (r.age_days || 0) + ' วัน'}${r.sla_breached ? ' <span style="color:var(--bad-ink);font-weight:600">เกิน SLA</span>' : ''}${r.contact ? ` · ${esc(r.contact)}` : ''}</div>
      <div style="font-size:13px;margin:3px 0">${r.text ? esc(r.text.slice(0, 160)) : '<i style="color:var(--text-muted,#64748b)">(ให้ดาวอย่างเดียว)</i>'}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap"><button onclick="msVoiceHandled(${r.id})" style="font-size:13px;padding:10px 14px;min-height:44px;border-radius:var(--r-md);border:1px solid #0f766e;background:#0f766e;color:#fff">✓ ตอบแล้ว</button>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener" style="font-size:13px;padding:10px 14px;min-height:44px;border-radius:var(--r-md);border:1px solid var(--border,#e2e8f0);color:var(--text-primary,#1e293b);text-decoration:none">เปิดต้นทาง ↗</a>` : ''}<button onclick="showFullDashboard('feedback')" style="font-size:13px;padding:10px 14px;min-height:44px;border-radius:var(--r-md);border:1px solid var(--border,#e2e8f0);background:transparent;color:var(--text-primary,#1e293b)">ตอบในแอป</button></div>
    </div>`).join('') : `<div style="font-size:13px;color:var(--text-muted,#64748b);padding:6px 0">ไม่มีรายการรอตอบ</div>`;
}
async function msVoiceHandled(id) {
  const note = await uiPrompt('ตอบลูกค้าอย่างไร (สรุปสั้น ๆ) — เว้นว่างได้');
  if (note === null) return;
  try {
    const r = await fetch(`${API}/api/feedback/${id}/handled`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note }) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    msLoadVoice();
  } catch (e) { uiNotice('บันทึกไม่สำเร็จ: ' + e.message); }
}

// ── shared pieces (the summary view and the branch sheet print the same cards) ──
const _msYoyOk = j => !!(j && j.has_yoy && j.range && j.range[1] >= '2026-05-15');   // same guard as the report
const _msTgtCol = p => p == null ? '#94a3b8' : p >= 100 ? '#10b981' : p >= 90 ? '#f59e0b' : '#ef4444';
const _msCpCol = cp => fcTargetCol(cp);   // ONE food-cost target (owner 2026-10-25): ≤ target green, above it red
const _msMuted = 'color:var(--text-muted,#64748b)';
const _msNone = t => `<div class="ms-row" style="font-size:13px;${_msMuted}">${t}</div>`;
// PR-PP2: the month in progress is compared to date - days 1..n against days 1..n of last month and of the same
// month a year back (the report's headline.mom_basis === 'to_date'); the weekday-fair figure stays on the pace card
const _msDowNote = 'เดือนนี้ยังไม่ครบ จึงเทียบกับวันที่ 1 ถึงวันเดียวกันของเดือนก่อนและของปีก่อน';
const _msSM = v => (v >= 0 ? '+' : '−') + fmt(Math.abs(v || 0));
function msKpiTile(l, v, c, sub, span) {
  return `<div class="ms-kpi"${span ? ' style="grid-column:1/-1"' : ''}><p class="lbl">${l}</p><p class="val" style="${span ? '' : 'font-size:21px;'}color:${textInk(c)}">${v}</p><p class="sub">${sub || ''}</p></div>`;
}
// headline KPIs: revenue (+8-period columns), covers, spend/head, GP, discounts — MoM and YoY chips
function msKpisHtml(j) {
  const H = j.headline || {}, G = j.gp || {}, yoy = _msYoyOk(j);
  // 2026-10-29: chip + word = one unit (the word "เทียบปีก่อน" used to hang at a line end in a half-width tile); the full-width
  // revenue tile keeps the long words, the half tiles say เดือนก่อน / ปีก่อน
  const chips = (m, y, vs) => `<span class="ms-cmp"><span>${msPchip(m)} ${vs ? 'เทียบเดือนก่อน' : 'เดือนก่อน'}</span>`
    + (yoy && y != null ? `<span>${msPchip(y)} ${vs ? 'เทียบปีก่อน' : 'ปีก่อน'}</span>` : '') + '</span>';
  const tr = j.trend || [];
  const cols = tr.length >= 3 ? `<div style="margin-top:10px">${msColumns(tr.map(x => x.revenue ?? null), { partial: j.partial ? tr.length - 1 : -1, labels: tr.map(x => x.label), fmt, grain: 'month', height: 92, aria: 'รายได้รายเดือน' })}</div>` : '';
  // PR-PD (owner 2026-10-29, no. 16) · colour means one thing: a figure is neutral, the pill beside it carries the direction;
  // red only where money is lost or a target is missed (the discount above its ceiling, the food cost above its target)
  const N = 'var(--text-primary)';
  const discOver = H.discount_policy_pct != null && H.discount_pct != null && H.discount_pct > H.discount_policy_pct;
  return `<div class="ms-kpi" style="grid-column:1/-1"><p class="lbl">รายได้${j.partial ? ' (สะสม)' : ''}</p><p class="val" style="color:${N}">${fmt(H.revenue || 0)}</p><p class="sub">${chips(H.rev_wow_pct, H.rev_yoy_pct, true)}</p>${cols}</div>`
    + msKpiTile('ลูกค้า', fmtN(H.customers || 0), N, chips(H.cust_wow_pct, H.cust_yoy_pct))
    + msKpiTile('รายได้/หัว', H.rev_per_head != null ? fmt(H.rev_per_head) : '—', N, chips(H.rph_wow_pct, H.rph_yoy_pct))
    + (canSeeCost() ? msKpiTile('กำไรขั้นต้น (GP)', G.gp != null ? fmt(G.gp) : '—', N, G.gp_pct != null ? `<b>${G.gp_pct.toFixed(0)}%</b> ${G.gp_pct_delta_pp != null ? msPchip(G.gp_pct_delta_pp, true, 'pp') : ''}` : `<span style="${_msMuted}">ไม่มีข้อมูลต้นทุน</span>`) : '')   // PL3: no gross profit for a role without a cost or profit module (the server drops it too)
    + msKpiTile('ส่วนลด', fmt(H.discount || 0), discOver ? 'var(--bad-ink)' : N, H.discount_pct != null ? `<b style="color:${discOver ? 'var(--bad-ink)' : N}">${H.discount_pct.toFixed(1)}%</b> <span style="${_msMuted};font-size:13px">ของยอด${H.discount_policy_pct != null ? ` · เพดาน&nbsp;${H.discount_policy_pct}%${discOver ? ' · เกินเพดาน' : ''}` : ''}</span>` : '', !canSeeCost())
    + (canSeeCost() ? msFoodCostTile(j) : '')   // after ส่วนลด so GP + ส่วนลด share a row (2026-10-29)
    + msWeakDayLine(j)
    + (H.mom_basis === 'to_date' ? `<p class="ms-dow-note" style="grid-column:1/-1;font-size:13px;${_msMuted};margin:0">${_msDowNote}</p>` : '');
}
// PR-PD no. 6 · ต้นทุนอาหาร against the ONE target (owner 2026-10-25: ≤ 58 %), and the dish that pushed it most this month in baht
// (menu.risers[0] x covers, the to-do line's figure). The กระจุก / ทั้งเครือ tag of that dish comes from the spike card's answer
// (/api/sales/menu-spike, PR-SK2 concentration) once it loads - it decides whether to call one branch or fix the chain standard.
function msFoodCostTile(j) {
  const P = j.pace || {}, M = j.menu || {}, fc = P.food_cost_pct;
  if (fc == null) return '';
  const fct = fcTargetSet(j.food_cost_target), over = fc > fct;
  const cov = M.covers || 0, r0 = (M.risers || []).filter(m => (m.delta || 0) > 0)[0];
  const drv = r0 && cov ? `ตัวดัน: ${esc(_msShortMenu(r0.menu))} <b style="color:var(--bad-ink)">+฿${fmt(r0.delta * cov)}</b> <span id="msFcTag" class="ms-fc-tag"></span>` : '';
  if (r0) setTimeout(() => msLoadFocusTag(j, r0.menu), 0);
  return `<div class="ms-kpi ms-fc-tile" style="grid-column:1/-1"><p class="lbl">ต้นทุนอาหาร <span style="${_msMuted}">· เป้า ≤${fcTargetTxt(fct)}%</span></p>`
    + `<p class="val" style="color:${over ? 'var(--bad-ink)' : 'var(--good-ink)'}">${fc.toFixed(1)}% <span class="ms-fc-badge">${over ? 'เกินเป้า' : 'ในเป้า'}</span>${P.food_cost_pct_prev != null ? ` <span style="font-size:13px;font-weight:400;${_msMuted}">เดือนก่อน ${P.food_cost_pct_prev.toFixed(1)}%</span>` : ''}</p>`
    + (drv ? `<p class="sub" style="font-size:13px">${drv}</p>` : '') + '</div>';
}
const _msShortMenu = n => String(n || '').replace(/^\((?:A|B|BF|AL|DL|TW)\)\s*/, '').replace(/\s*\(1 [^)]*\)\s*$/, '');
const _msNorm = n => String(n || '').toLowerCase().replace(/^\((?:a|b|bf|al|dl|tw)\)\s*/, '').replace(/[,.·\-_\s\u00a0()]/g, '');
let _msSpikeCache = {};
async function msLoadFocusTag(j, menu) {
  const key = (j.selected_month || '') + '|' + (msBranch() || '');
  try {
    if (!_msSpikeCache[key]) {
      const q = new URLSearchParams({ period: 'mtd', threshold: '1.5', limit: '300', slim: '1' });   // names + กระจุก tag only (the full answer is ~750 KB)
      if (msBranch()) q.set('branch', msBranch());
      if (j.range && j.range[1]) q.set('date', j.range[1]);
      _msSpikeCache[key] = api(`${API}/api/sales/menu-spike?${q}`);
    }
    const d = await _msSpikeCache[key];
    const el = document.getElementById('msFcTag'); if (!el || !d || !d.dishes) return;
    const want = _msNorm(menu), hit = d.dishes.find(x => _msNorm(x.menu) === want) || d.dishes.find(x => _msNorm(x.menu).includes(want) || want.includes(_msNorm(x.menu)));
    const c = hit && hit.concentration; if (!c) return;
    const share = `${Math.round(c.share_pct)}%`;
    el.className = `ms-fc-tag ms-fc-tag-${c.spread}`;
    el.textContent = c.spread === 'wide' ? `ทั้งเครือ ${fmtN(c.up_branches)} สาขา` : c.spread === 'one' ? `สาขาเดียว ${c.branch}` : `กระจุก ${c.branch} ${share}`;
    el.title = c.spread === 'wide' ? `เพิ่มกระจาย ${c.up_branches} สาขา — แก้ที่มาตรฐานการตักทั้งเครือ` : `ส่วนที่เพิ่มอยู่ที่ ${c.branch} ${share} — คุยกับสาขานี้`;
  } catch (e) { delete _msSpikeCache[key]; }
}
// PR-PD no. 5 · the weekday that fell against the comparison period (peak.weak_day): a weekday promotion or a roster change,
// not 28 branch calls
const _MS_DOW_TH = { Mon: 'วันจันทร์', Tue: 'วันอังคาร', Wed: 'วันพุธ', Thu: 'วันพฤหัสบดี', Fri: 'วันศุกร์', Sat: 'วันเสาร์', Sun: 'วันอาทิตย์' };
function msWeakDayLine(j) {
  const w = (j.peak || {}).weak_day; if (!w) return '';
  return `<p class="ms-weakday" style="grid-column:1/-1">${_MS_DOW_TH[w.dow] || w.dow}ตก <b style="color:var(--bad-ink)">▼ ${Math.abs(w.delta_pct).toFixed(0)}%</b> เทียบเดือนก่อน <span style="${_msMuted}">· ต่อวัน ${fmt(w.avg_revenue)} จาก ${fmt(w.prev_avg_revenue)}</span></p>`;
}
// target card: % of target as the headline, the bar, then the figures behind it
function msTargetHtml(j) {
  const P = j.pace || {}, pvt = P.proj_vs_target_pct, mc = P.month_complete, TS = P.target_scope;   // DV1: % of target like for like
  if (!P.target) return _msNone('ยังไม่มีเป้าเดือนนี้ — สาขาใหม่ยังไม่มีเดือนเดียวกันปีก่อน');
  const c = _msTgtCol(pvt);
  // PR-PD (owner 2026-10-29, no. 2) · the instruction behind the gap, before the figures: "เหลือ 9 วัน · ต้องขายวันละ ฿3.1M (ที่ผ่านมา ฿2.7M)";
  // red only when the needed pace is above the pace so far (the one red of this card), green when the target is within the current pace
  const RR = P.run_rate, rr = RR && RR.days_left > 0 && RR.need_per_day != null
    ? (() => { const hard = RR.need_per_day > (RR.normal_per_day || 0) * 1.02, done = RR.need_per_day <= 0;
        return `<div class="ms-runrate ${done ? 'is-good' : hard ? 'is-bad' : 'is-good'}"><span class="ms-runrate-l">เหลือ ${fmtN(RR.days_left)} วัน</span>`
          + (done ? `<b>ถึงเป้าแล้ว</b>` : `<b>ต้องขายวันละ ${fmt(RR.need_per_day)}</b>`)
          + `<span class="ms-runrate-s">ที่ผ่านมา ${fmt(RR.normal_per_day || 0)}/วัน${RR.branches && TS && TS.no_target_branches ? ` · ${fmtN(RR.branches)} สาขาที่มีเป้า` : ''}</span></div>`; })()
    : '';
  return `<div style="display:flex;align-items:baseline;gap:10px;padding:10px 0 4px"><span style="font-size:30px;font-weight:800;line-height:1;color:${textInk(c)}">${pvt != null ? pvt.toFixed(0) + '%' : '—'}</span><span style="font-size:13px;${_msMuted}">${mc ? 'ของเป้าทั้งเดือน' : 'ของเป้า (ประมาณการสิ้นเดือน)'}</span></div>${rr}
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">${msBar(Math.min(100, Math.round(pvt || 0)), c)}<span style="font-size:13px;${_msMuted}">100%</span></div>
    <div class="ms-row"><span style="flex:1;font-size:13px">${mc ? 'ยอดทั้งเดือน' : `ยอดสะสม (${P.days_elapsed || 0}/${P.days_total || 0} วัน)`}</span><b style="font-variant-numeric:tabular-nums">${fmt(P.mtd_revenue || 0)}</b></div>
    ${mc ? '' : `<div class="ms-row"><span style="flex:1;font-size:13px">ประมาณการสิ้นเดือน${P.proj_method === 'dow' ? ' <span style="font-size:13px;' + _msMuted + '">(ถ่วงวันในสัปดาห์)</span>' : ''}</span><b style="font-variant-numeric:tabular-nums">${fmt(P.projected || 0)}</b></div>`}
    ${TS && TS.no_target_branches ? `<div class="ms-row"><span style="flex:1;font-size:13px">${mc ? 'ยอด' : 'ประมาณการ'} ${TS.branches} สาขาที่มีเป้า <span style="font-size:13px;${_msMuted}">(ไม่รวม ${TS.no_target_branches} สาขาที่ยังไม่มีเป้า)</span></span><b style="font-variant-numeric:tabular-nums">${fmt(TS.projected || 0)}</b></div>` : ''}
    <div class="ms-row"><span style="flex:1;font-size:13px">เป้า ${P.month || ''}</span><b style="font-variant-numeric:tabular-nums">${fmt(P.target)}</b></div>
    ${P.mom_pct != null ? `<div class="ms-row"><span style="flex:1;font-size:13px">เทียบเดือนก่อน <span style="font-size:13px;${_msMuted}">${P.mom_basis === 'dow' ? '(ถ่วงวันในสัปดาห์)' : '(วันเท่ากัน)'}</span></span>${msPchip(P.mom_pct)}</div>` : ''}
    <p style="font-size:13px;${_msMuted};margin:6px 0 4px">เป้า = ตัวเลขที่ตั้งไว้ หรือยอดจริงของเดือนเดียวกันปีก่อน</p>`;
}
// phone B (owner 2026-10-30, item 3) · ONE row pattern for ต้องทำ, ตรวจวันนี้ and the branch lists: line 1 = the bold title and the baht
// on the right; line 2 = the branch chips (a tap opens the branch sheet) and one short fact; the long explanation sits behind a tap on
// the row (it expands) - never a three-line paragraph per item. A row about one branch opens that branch's sheet instead.
// The bar on the left: red = money lost or a target missed, amber = ต้องดู, green = ok (index.html .ms-r2).
// 2026-10-31 (phone review set 2, item 3): the row is the app's ONE list row, uiRow2 (js/01-core.js); these names stay for the summary's callers
const msRow2 = o => uiRow2(o);
function msRow2Toggle(el, e) { uiRow2Toggle(el, e); }
function msRow2Key(el, e) { uiRow2Key(el, e); }
// the branch chips of a row: three, then "+N"
function _msBrChips(brs) {
  return (brs || []).slice(0, 3).map(b => `<span class="ms-watch-br" data-br="${esc(b)}" onclick="event.stopPropagation();msOpenBranch(this.dataset.br)">${esc(b)}</span>`).join('')
    + ((brs || []).length > 3 ? `<span>+${brs.length - 3}</span>` : '');
}
// a to-do sentence of the report ("ปลวกแดง ยอดขายลดลง 40% เทียบเดือนก่อน (฿1.2M)", "3 สาขาต่ำกว่า 90% ของเป้าเดือนนี้: A 70%, B 80%",
// "… — ตรวจสอบสาเหตุ") → the branches it names (chips), a short title without them, the list or the advice; the whole sentence stays behind the tap
function _msSplitLine(text, names) {
  const t = String(text || '').trim(), brs = [];
  const lead = s => names.find(n => s === n || s.startsWith(n + ' ') || s.startsWith(n + ','));   // names run longest first
  let head = t.split(' — ')[0];
  const ci = head.indexOf(': ');
  if (ci > 0) { head.slice(ci + 2).split(/,\s*/).forEach(p => { const n = lead(p.trim()); if (n && !brs.includes(n)) brs.push(n); }); head = head.slice(0, ci); }
  const n0 = lead(head);
  if (n0) { if (!brs.includes(n0)) brs.unshift(n0); head = head.slice(n0.length).trim(); }
  // the title keeps the finding: no "(…)" aside, no baht figure (the baht is on the right), nothing after " · "
  head = head.split(' · ')[0].replace(/\s*\([^)]*\)/g, '').replace(/\s*฿[\d,.]+[KM]?/g, '').replace(/\s+/g, ' ').trim() || t;
  return { title: head, branches: brs, more: head !== t };
}
// DV6 (phone table review 2026-10-25, top-10 #9): every to-do line shows the baht it is about on the right (actions[].baht, signed:
// red = sales lost / money given away / cost added, green = the opposite; dvBaht, js/01-core.js) with what the figure is (a.baht_label) under it
// owner 2026-10-31 ("กดแล้วทำงานต่อได้เลย"): a tap on the row goes where the work is done (actions[].go, msActionGo); the ▾ expands the sentence;
// a branch chip opens that branch's sheet
function msActionsHtml(j) {
  const names = (j.branches || []).map(x => x.branch).filter(Boolean).sort((x, y) => y.length - x.length);
  return (j.actions || []).map(a => {
    const b = a.baht, has = b != null && b !== 0, L = _msSplitLine(a.text, names), go = a.go && a.go.kind ? a.go : null;
    return msRow2({ cls: 'ms-act', tone: (has && b < 0) || a.sev === 'high' ? 'bad' : 'watch', title: esc(L.title), val: has ? dvBaht(b) : '', valTone: has ? (b < 0 ? 'bad' : 'good') : '',
      chips: _msBrChips(L.branches), fact: esc(a.baht_label || ''), more: L.more ? esc(a.text) : '',
      onGo: go ? 'msActionGo(JSON.parse(this.dataset.go))' : '', attrs: go ? ` data-go="${esc(JSON.stringify(go))}" data-go-kind="${esc(go.kind)}"` : '' });
  }).join('') || _msNone('ไม่พบประเด็นเร่งด่วน');
}
// ── a ต้องทำ row's tap (actions[].go, backend/routers/reports.py): the place the work is done, with the branch / dish / sort already chosen ──
// branch → the branch sheet · ms → a detail screen of this summary (pushed on the history, so back returns to ต้องทำ), then its focus is
// tinted for 2 s (uiFlash): the leak card (cash_heavy / heavy / reprint) and the branch's row, the เป้า sort of สาขา, the dish's row of เมนู ·
// tab → a page of the app
const _MS_FOCUS_CARD = { cash_heavy: 'ลดแล้วจ่ายเงินสด', heavy: 'ลดหนัก', reprint: 'บิลพิมพ์ซ้ำ' };
async function msActionGo(go) {
  if (!go || !go.kind) return;
  if (go.kind === 'branch') { if (go.branch) msOpenBranch(go.branch); return; }
  if (go.kind === 'tab') {
    if (!go.tab) return;
    if (phOn() && typeof phOpenPage === 'function') await phOpenPage(() => (typeof deptOpenPage === 'function' ? deptOpenPage({ tab: go.tab }) : switchTab(go.tab)));
    else await showFullDashboard(go.tab);
    return;
  }
  if (go.kind !== 'ms') return;
  const v = _PH_DETAIL[go.view] ? go.view : 'month';
  if (v === 'branches' && go.focus === 'target') _msBranchSort = 'tgt';      // msRenderBranches falls back to ยอด when no branch has a target
  if (phOn() && _phKind() === 'centre') {
    if (_phView === 'summary' && _phDetail) {                                // from ต้องทำ: a new history entry, so back returns here
      try { history.pushState({ phd: v }, ''); _phDetailPushed = true; } catch (e) { /* no history: the back arrow goes to the cards */ }
      _phShowDetail(v);
    } else phOpenDetail(v);
  } else msSwitchView(_PH_DETAIL[v].v);
  await msGoFocus(v, go);
}
// wait (≤ 2 s) for the element a go points at - the เมนู list of a role without cost loads after the screen opens
async function _msWaitFor(fn) {
  for (let i = 0; i < 20; i++) { const el = fn(); if (el) return el; await new Promise(r => setTimeout(r, 100)); }
  return null;
}
async function msGoFocus(v, go) {
  await new Promise(r => requestAnimationFrame(() => r()));
  if (v === 'discounts' && go.focus) {
    const word = _MS_FOCUS_CARD[go.focus];
    const card = word && [...document.querySelectorAll('#msDiscCards .ms-kpi')].find(c => ((c.querySelector('.lbl') || {}).textContent || '').startsWith(word));
    if (!uiFlash(card)) {                                                    // no card (an unavailable leak): the section's heading
      const h = [...document.querySelectorAll('#msv-discounts .ms-section-title')].find(t => /รั่วไหล/.test(t.textContent || ''));
      if (h) h.scrollIntoView({ block: 'start' });
    }
    if (go.branch) {
      const row = [...document.querySelectorAll('#msDiscBranches [data-br]')].find(r => /** @type {HTMLElement} */ (r).dataset.br === go.branch);
      if (row) { row.classList.add('ui-hl'); setTimeout(() => row.classList.remove('ui-hl'), 2000); }
    }
  } else if (v === 'branches' && go.focus === 'target') {
    const seg = document.getElementById('msSortTgt');
    if (seg && seg.style.display !== 'none') uiFlash(seg);
  } else if (v === 'menu' && go.menu) {
    const want = _msNorm(go.menu);
    const nameOf = r => { const n = r.querySelector('.ms-menu-name'); return _msNorm(n ? (n.getAttribute('title') || n.textContent) : String((r.querySelector('.ms-r2-t') || {}).textContent || '').replace(/^\d+\.\s*/, '')); };
    const row = await _msWaitFor(() => [...document.querySelectorAll('#msMenuRisers .ui-r2')].find(r => nameOf(r) === want)
      || [...document.querySelectorAll('#msMenuRisers .ui-r2')].find(r => { const n = nameOf(r); return n && (n.includes(want) || want.includes(n)); }));
    if (row && /** @type {HTMLElement} */ (row).hidden) {                  // past the first rows: show them all first
      const more = document.querySelector('#msMenuRisers .dv-more[aria-expanded="false"]'); if (more) dvMoreToggle(more);
    }
    uiFlash(row);
  }
}
// PR-PW (owner 2026-10-29, no. 7-10, 12-14) · ตรวจวันนี้: payload.watch.items, each a finding with its level (fix / watch / ok / none), the
// sentence that names the branches and the action, and the rows behind it. Sorted by level on the server. Unavailable items fold
// into one grey row (their reasons behind a tap), so a month with no issue slips still reads as a short card. Phone B: the two-line rows.
// 2026-10-31 (owner) · บิลยกเลิก / พิมพ์ซ้ำ: the item carries three windows (windows.day / week / month, each the item's own shape, the
// numerator and the bills over one range); chips เมื่อวาน · 7 วัน · 15 วัน · เดือนนี้ switch the row client-side, the pick is kept for the session
const _MS_VW = [['day', 'เมื่อวาน'], ['week', '7 วัน'], ['half', '15 วัน'], ['month', 'เดือนนี้']];
function _msVoidKey(it) {
  let k = 'month';
  try { k = sessionStorage.getItem('msVoidWin') || 'month'; } catch (e) { /* private mode: the default */ }
  return ((it || {}).windows || {})[k] ? k : 'month';
}
function msSetVoidWin(k) {
  try { sessionStorage.setItem('msVoidWin', k); } catch (e) { /* private mode: this render only */ }
  if (_msRpt) msRenderWatch(_msRpt);
}
function _msVoidItem(it) {
  if (!it || it.key !== 'voids' || !it.windows) return it;
  const k = _msVoidKey(it), w = it.windows[k] || it;
  return { ...it, ...w, key: 'voids', title: it.title, available: !!(it.available || w.available), _vk: k };
}
function _msVoidChips(it) {
  const W = it.windows || {};
  if (_MS_VW.filter(([k]) => W[k]).length < 2) return '';
  return `<div class="ms-vw-chips" role="group" aria-label="ช่วงเวลาของบิลยกเลิก">` + _MS_VW.filter(([k]) => W[k]).map(([k, t]) =>
    `<button type="button" class="ms-vw-chip" aria-pressed="${k === it._vk}" onclick="msSetVoidWin('${k}')">${t}</button>`).join('') + '</div>';
}
function msRenderWatch(j) {
  const wrap = document.getElementById('msWatchWrap'), el = document.getElementById('msWatch'), cnt = document.getElementById('msWatchCount');
  if (!wrap || !el) return;
  const W = j.watch;
  if (!W || !(W.items || []).length) { wrap.style.display = 'none'; return; }
  const items = W.items.map(_msVoidItem), ord = { fix: 0, watch: 1, ok: 2, none: 3 };
  const live = items.filter(it => it.available).sort((a, b) => (ord[a.level] ?? 9) - (ord[b.level] ?? 9)), dead = items.filter(it => !it.available);
  const tone = l => l === 'fix' ? 'bad' : l === 'watch' ? 'watch' : l === 'ok' ? 'good' : '';
  const row = it => {
    const line = String(it.line || ''), act = it.level === 'fix' || it.level === 'watch';
    let fact = line.split(' — ')[0].split(': ')[0];                         // the finding without the branch list and the advice
    if (it.key === 'voids' && it.source === 'month') fact += ' · ยกเลิกนับทั้งเดือน';
    const baht = act ? (it.rows || []).reduce((s, r) => s + (+r.baht > 0 ? +r.baht : 0), 0) : 0;
    const title = esc(it.title) + (it.key === 'voids' && it.label ? ` · ${esc(it.label)}` : '');
    // 2026-10-31 (owner): a tap on the voids row opens its drill-down (every branch, every bill); the ▾ still expands the sentence
    return (it.key === 'voids' ? _msVoidChips(it) : '') + msRow2({ cls: 'ms-watch-r', tone: tone(it.level), title, val: baht ? '฿' + fmt(baht) : '', valTone: baht ? 'bad' : '',
      chips: act ? _msBrChips((it.rows || []).map(r => r.branch).filter(Boolean)) : '', fact: esc(fact), more: fact !== line ? esc(line) : '',
      onGo: it.key === 'voids' ? 'msOpenVoids()' : '', attrs: it.key === 'voids' ? ' data-go-kind="voids"' : '' });
  };
  const deadHtml = !dead.length ? '' : msRow2({ cls: 'ms-watch-r ms-watch-none', title: `ยังไม่มีข้อมูล ${fmtN(dead.length)} รายการ`, fact: esc(dead.map(it => it.title).join(' · ')),
    more: dead.map(it => `<b>${esc(it.title)}</b>: ${esc(it.line || '')}`).join('<br>') });
  el.innerHTML = live.map(row).join('') + deadHtml;
  const nAct = items.filter(it => it.level === 'fix' || it.level === 'watch').length;   // follows the chosen voids window
  if (cnt) cnt.textContent = nAct ? `· ต้องดู ${fmtN(nAct)}` : '';
  wrap.style.display = '';
}
// ── บิลยกเลิก / พิมพ์ซ้ำ drill-down (owner 2026-10-31: "drill down ... ดูรายละเอียดทุกสาขาได้เลย รายสาขารายบิลเลย") ──
// /api/reports/voids (routers/voids.py, the card's own counts and windows): level 1 = every branch of the window, level 2 = one branch's
// cancelled bills and reprinted bills, level 3 = one bill (the row sheet). A tap on the card's row opens level 1 (the detail screen `voids`);
// a branch row pushes a history entry (back returns to level 1 at its scroll); with one branch in the picker the screen opens on level 2.
// The window chips are the card's (sessionStorage msVoidWin).
let _msVoidBr = '', _msVoidY = 0, _msVoidRestore = false, _msVoidAnchor = '', _msVoidData = null, _msVoidSeq = 0;
const _msVoidCache = {};
function _msVoidWin() {
  let k = 'month';
  try { k = sessionStorage.getItem('msVoidWin') || 'month'; } catch (e) { /* private mode: the default */ }
  return _MS_VW.some(([x]) => x === k) ? k : 'month';
}
function msOpenVoids() {
  _msVoidBr = msBranch() || '';
  msActionGo({ kind: 'ms', view: 'voids' });
}
// the history entry of a drill level: {phd: 'voids', vb: branch} - the shell's popstate (phone B) and the one below read it
function _msVoidFromState(st) {
  _msVoidRestore = !st.vb;
  _msVoidBr = st.vb || msBranch() || '';
}
function msVoidsBranch(name) {
  if (!name) return;
  _msVoidY = window.scrollY;
  try { history.pushState({ phd: 'voids', vb: name }, ''); _phDetailPushed = true; } catch (e) { /* no history: back goes to the cards */ }
  _msVoidBr = name;
  if (typeof phTopRender === 'function') phTopRender();
  window.scrollTo(0, 0);
  msLoadVoids();
}
// level 2's class filter: ไม่ออกใหม่ (the alert's basis, the default) · ออกใหม่แล้ว · ทั้งหมด - kept for the session
function _msVoidCls() {
  let k = 'open';
  try { k = sessionStorage.getItem('msVoidCls') || 'open'; } catch (e) { /* private mode: the default */ }
  return ['all', 'open', 're'].includes(k) ? k : 'open';
}
function msVoidsCls(k) {
  try { sessionStorage.setItem('msVoidCls', k); } catch (e) { /* private mode: this render only */ }
  const box = document.getElementById('msVoids');
  if (box && _msVoidData && _msVoidData.level === 2) box.innerHTML = msVoidsL2Html(_msVoidData);
}
function msVoidsWin(k) {
  try { sessionStorage.setItem('msVoidWin', k); } catch (e) { /* private mode: this render only */ }
  msLoadVoids();
}
// the "?" of the screen: the bands and what a reprint is
function _msVoidHelp(j) {
  const B = j.bands || {};
  return uiQ('voids-drill', 'บิลยกเลิก / พิมพ์ซ้ำ', `<p><b>อัตรายกเลิก</b> = บิลยกเลิกที่ไม่ออกใหม่ ÷ (บิลขาย + บิลยกเลิก) ในช่วงเวลาที่เลือก</p>`
    + `<p><b style="color:var(--bad-ink)">ต้องแก้</b> ตั้งแต่ ${B.fix}% · <b style="color:var(--amber-ink)">ต้องดู</b> ตั้งแต่ ${B.watch}%</p>`
    + `<p>ช่วง "เมื่อวาน" ต้องมีอย่างน้อย ${j.min_bills_day} บิลจึงสรุปผล</p>`
    + `<p><b>ออกใหม่แล้ว</b> = มีบิลใหม่ที่สาขาเดียวกันภายใน ${j.reissue_min || 30} นาทีหลังยกเลิก จำนวนลูกค้าเท่ากัน หรือยอดต่างไม่เกิน 20% (เป็นการแก้บิล) · `
    + `<b>ไม่ออกใหม่</b> = ไม่พบบิลใหม่ · อัตราและการเตือนนับเฉพาะ <b>ไม่ออกใหม่</b> รายละเอียดแสดงทุกบิล</p>`
    + `<p><b>บิลเปล่า</b> = บิลยกเลิกยอด ฿0 ไม่ใช่เงินที่หาย จึงไม่นับในการเตือนและไม่นับเป็นออกใหม่ แสดงในรายละเอียดเท่านั้น</p>`
    + `<p><b>บิลพิมพ์ซ้ำ</b> = บิลที่มีส่วนลดและพิมพ์ใบเสร็จมากกว่า 1 ครั้ง</p>`
    + `<p>บิลยกเลิกไม่มีรายการอาหาร เพราะระบบ POS ไม่ส่งรายการของบิลที่ยกเลิก</p>`);
}
function _msVoidChipsHtml(j) {
  const have = j.windows || ['month'];
  return `<div class="ms-vw-chips" role="group" aria-label="ช่วงเวลาของบิลยกเลิก">` + _MS_VW.filter(([k]) => have.includes(k)).map(([k, t]) =>
    `<button type="button" class="ms-vw-chip" aria-pressed="${k === j.window}" onclick="msVoidsWin('${k}')">${t}</button>`).join('') + '</div>';
}
const _msVoidTone = l => l === 'fix' ? 'bad' : l === 'watch' ? 'watch' : '';
const _msHm = at => String(at || '').slice(11, 16);
const _msDayTh = at => { const d = String(at || '').slice(0, 10); return d ? `${+d.slice(8, 10)} ${_MS_TH_MON[+d.slice(5, 7) - 1] || ''}` : ''; };
async function msLoadVoids() {
  const box = document.getElementById('msVoids'); if (!box) return;
  const seq = ++_msVoidSeq, br = _msVoidBr;
  const q = new URLSearchParams({ window: _msVoidWin(), lang: 'th' });
  const mon = msMonth(); if (mon) q.set('month', mon);
  if (br) { q.set('branch', br); if (br !== msBranch() && _msVoidAnchor) q.set('date', _msVoidAnchor); }   // level 2 reads level 1's window
  const key = q.toString();
  if (!_msVoidCache[key]) box.innerHTML = uiSkel(6);
  let j;
  try { j = _msVoidCache[key] || await api(`${API}/api/reports/voids?${key}`); }
  catch (e) { if (seq === _msVoidSeq) box.innerHTML = uiEmpty({ t: 'โหลดไม่สำเร็จ', act: 'ลองอีกครั้ง', go: 'msLoadVoids()' }); return; }
  if (seq !== _msVoidSeq) return;
  if (!j || !j.available) { box.innerHTML = uiEmpty({ t: (j && j.line) || 'ยังไม่มีข้อมูลบิลยกเลิก' }); return; }
  _msVoidCache[key] = j;
  if (j.level === 1 && j.anchor) _msVoidAnchor = j.anchor;
  _msVoidData = j;
  box.innerHTML = j.level === 2 ? msVoidsL2Html(j) : msVoidsL1Html(j);
  if (_msVoidRestore && j.level === 1) { const y = _msVoidY; requestAnimationFrame(() => window.scrollTo(0, y)); }
  _msVoidRestore = false;
}
function _msVoidHead(j, T, title) {
  const tone = _msVoidTone(T.level);
  const r = T.rate_open_pct != null ? T.rate_open_pct : T.rate_pct;
  const rate = r != null ? `<b class="ms-vd-rate${tone ? ' is-' + tone : ''}">${r}%</b>` : '—';
  return `<div class="ms-vd-head"><span class="ms-vd-h1">${esc(title)} · ${esc(j.label || '')}</span>${_msVoidHelp(j)}</div>`
    + `<p class="ms-vd-sum">ยกเลิกแล้วไม่ออกใหม่ ${rate} · ${fmtN(T.voids_open != null ? T.voids_open : T.voids)} บิล · ฿${fmtN(T.amount_open != null ? T.amount_open : T.amount)}`
    + ` · <span class="ms-vd-all">รวมยกเลิกทั้งหมด ${fmtN(T.voids)} บิล</span> · พิมพ์ซ้ำ ${fmtN(T.reprints)} บิล</p>`
    + (j.source === 'month' ? `<p class="ms-vd-note">นับบิลยกเลิกทั้งเดือน (ไม่มีข้อมูลรายวันของช่วงนี้)</p>` : '');
}
// level 1 · every branch: company, then franchise; each sorted by the rate, the branches with no void at the bottom (grey)
function msVoidsL1Html(j) {
  const rows = j.rows || [], T = j.total || {}, B = j.bands || {};
  if (j.source === 'none') return _msVoidChipsHtml(j) + uiEmpty({ t: 'ยังไม่มีข้อมูลบิลยกเลิกรายวันของช่วงนี้' });
  const max = Math.max(1, ...rows.map(r => r.rate_open_pct || 0));
  const row = r => {
    const zero = !r.voids, tone = zero ? '' : _msVoidTone(r.level);
    const col = tone === 'bad' ? 'var(--bad-ink)' : tone === 'watch' ? 'var(--amber-ink)' : 'var(--text-muted)';
    return uiRow2({ cls: 'ms-vd-br' + (zero ? ' ms-vd-zero' : ''), tone, title: esc(r.branch),
      val: r.rate_open_pct != null ? r.rate_open_pct + '%' : '—', valTone: tone,
      fact: `ไม่ออกใหม่ <b>${fmtN(r.voids_open)}</b> บิล · ฿${fmtN(r.amount_open)} · <span class="ms-vd-all">ทั้งหมด ${fmtN(r.voids)}</span> · พิมพ์ซ้ำ ${fmtN(r.reprints)}${r.few ? ' · บิลน้อย' : ''}`,
      bar: zero ? null : { pct: 100 * (r.rate_open_pct || 0) / max, col },
      onGo: 'msVoidsBranch(this.dataset.br)', attrs: ` data-br="${esc(r.branch)}"` });
  };
  const grp = (t, list) => list.length ? `<div class="ms-section-title ms-vd-grp">${t} <span>${fmtN(list.length)} สาขา</span></div><div class="ms-card ms-r2-list">${list.map(row).join('')}</div>` : '';
  const tr = T.rate_open_pct != null ? T.rate_open_pct : T.rate_pct;
  const lv = tr == null ? '' : tr >= B.fix ? 'fix' : tr >= B.watch ? 'watch' : '';
  return _msVoidChipsHtml(j) + _msVoidHead(j, { ...T, level: lv }, 'ทุกสาขา')
    + (rows.length ? grp('สาขาบริษัท', rows.filter(r => r.type !== 'franchise')) + grp('สาขาแฟรนไชส์', rows.filter(r => r.type === 'franchise'))
      : uiEmpty({ t: 'ไม่มีสาขาในช่วงนี้' }));
}
// level 2 · one branch: the header, the reasons, every cancelled bill by day (newest first, 20 then ดูทั้งหมด), every reprinted bill
function msVoidsL2Html(j) {
  const H = j.header || {}, N = H.normal;
  const head = _msVoidChipsHtml(j) + _msVoidHead(j, H, j.branch)
    + (N ? `<p class="ms-vd-note">ปกติของสาขา ${N.rate_open_pct != null ? N.rate_open_pct : N.rate_pct}% (8 สัปดาห์ก่อนหน้า)</p>` : '');
  const reasons = (j.reasons || []).length ? `<div class="ms-vd-reasons" role="list">` + j.reasons.map(r =>
    `<span class="ms-vd-reason" role="listitem">${esc(r.reason)} <b>${fmtN(r.n)}</b></span>`).join('') + '</div>' : '';
  const cls = _msVoidCls(), all = j.bills || [], CAP = 20;
  // owner 2026-10-31 ("ตัด"): a ฿0 void is บิลเปล่า - its own class, not in the alert, listed under ทั้งหมด and its own chip
  const bills = all.filter(b => cls === 'all' || (cls === 'open' ? !b.reissued_as && !b.zero : cls === 'zero' ? !!b.zero : !!b.reissued_as));
  const nOpen = all.filter(b => !b.reissued_as && !b.zero).length, nZero = all.filter(b => b.zero).length;
  const filt = all.length ? `<div class="ms-vw-chips ms-vd-cls-chips" role="group" aria-label="ประเภทบิลยกเลิก">` + [['all', 'ทั้งหมด', all.length], ['open', 'ไม่ออกใหม่', nOpen], ...(nZero ? [['zero', 'บิลเปล่า', nZero]] : []), ['re', 'ออกใหม่แล้ว', all.length - nOpen]].map(([k, t, n]) =>
    `<button type="button" class="ms-vw-chip" aria-pressed="${k === cls}" onclick="msVoidsCls('${k}')">${t} ${fmtN(n)}</button>`).join('') + '</div>' : '';
  let lastDay = '', billsHtml = '';
  bills.forEach((b, i) => {
    const day = String(b.at || '').slice(0, 10), attr = dvMoreAttr('vd-bills', i, bills.length, CAP);
    if (day !== lastDay) {
      lastDay = day;
      const same = bills.filter(x => String(x.at || '').slice(0, 10) === day);
      billsHtml += `<div class="ms-vd-day"${attr}>${_msDayTh(b.at)} <span>${fmtN(same.length)} บิล · ฿${fmtN(same.reduce((s, x) => s + (x.amount || 0), 0))}</span></div>`;
    }
    const k = all.indexOf(b);
    billsHtml += uiRow2({ cls: 'ms-vd-bill', tone: b.reissued_as || b.zero ? '' : 'bad', title: `${_msHm(b.at)} · ${esc(b.receipt || 'ไม่มีเลขที่บิล')}`, val: '฿' + fmtN(b.amount),
      chips: b.zero ? '<span class="ms-vd-cls">บิลเปล่า ฿0</span>' : b.reissued_as ? `<span class="ms-vd-cls">ออกใหม่แล้ว → ${esc(b.reissued_no || b.reissued_as)}</span>` : '<span class="ms-vd-cls is-open">ไม่ออกใหม่</span>',
      fact: esc([b.reason, b.user].filter(Boolean).join(' · ')) + (b.discount > 0 ? ` · มีส่วนลด ฿${fmtN(b.discount)}` : ''),
      onGo: `msVoidBill('b',${k})`, attrs: attr });
  });
  const reps = j.reprints || [];
  const repHtml = reps.map((r, i) => uiRow2({ cls: 'ms-vd-bill', tone: 'watch', title: `${_msDayTh(r.at)} ${_msHm(r.at)} · ${esc(r.no || r.receipt)}`, val: '฿' + fmtN(r.total),
    fact: `พิมพ์ ${fmtN(r.prints)} ครั้ง · ส่วนลด ฿${fmtN(r.discount)}${r.cashier ? ' · ' + esc(r.cashier) : ''}`,
    onGo: `msVoidBill('r',${i})`, attrs: dvMoreAttr('vd-reps', i, reps.length, CAP) })).join('');
  const more = n => n ? `<p class="ms-vd-note">และอีก ${fmtN(n)} บิล</p>` : '';
  return head
    + `<div class="ms-section-title ms-vd-grp">บิลยกเลิก <span>ไม่ออกใหม่ ${fmtN(H.voids_open)} · ทั้งหมด ${fmtN(H.voids)} บิล · ฿${fmtN(H.amount)}</span></div>`
    + (all.length ? reasons + filt + (bills.length ? `<div class="ms-card ms-r2-list">${billsHtml}</div>${dvMoreBtn('vd-bills', bills.length, CAP)}` : uiEmpty({ t: 'ไม่มีบิลประเภทนี้ในช่วงนี้', icon: 'check' })) + more(j.bills_more)
      : uiEmpty({ t: j.source === 'detail' ? 'ไม่มีบิลยกเลิกในช่วงนี้' : 'ไม่มีรายการบิลยกเลิกรายบิลของช่วงนี้', icon: 'check' }))
    + `<div class="ms-section-title ms-vd-grp">บิลพิมพ์ซ้ำ <span>${fmtN(H.reprints)} บิล · ส่วนลด ฿${fmtN(H.reprint_baht)}</span></div>`
    + (reps.length ? `<div class="ms-card ms-r2-list">${repHtml}</div>${dvMoreBtn('vd-reps', reps.length, CAP)}${more(j.reprints_more)}`
      : uiEmpty({ t: 'ไม่มีบิลพิมพ์ซ้ำในช่วงนี้', icon: 'check' }));
}
// level 3 · one bill in the row sheet: a cancelled bill = its fields (the POS keeps no lines of a cancelled bill); a reprinted bill = its
// lines and payments (/api/reports/voids/bill)
async function msVoidBill(kind, i) {
  const j = _msVoidData; if (!j || j.level !== 2) return;
  const b = (kind === 'b' ? j.bills : j.reprints || [])[i]; if (!b) return;
  const sh = document.getElementById('msSheet'), body = document.getElementById('msSheetBody'); if (!sh || !body) return;
  const kv = pairs => `<dl class="ph-rs-list">${pairs.filter(p => p[1] !== '' && p[1] != null).map(([h, v]) => `<div class="ph-rs-row"><dt>${esc(h)}</dt><dd>${v}</dd></div>`).join('')}</dl>`;
  document.getElementById('msSheetTitle').textContent = kind === 'b' ? 'บิลยกเลิก ' + (b.receipt || '') : 'บิลพิมพ์ซ้ำ ' + (b.no || b.receipt);
  document.getElementById('msSheetSub').textContent = `${j.branch} · ${_msDayTh(b.at)} ${_msHm(b.at)}`;
  sh.classList.add('open', 'ph-rowsheet'); sh.scrollTop = 0; document.body.classList.add('ms-sheet-open');
  const back = document.getElementById('phSheetBack'); if (back) back.hidden = false;
  if (kind === 'b') {
    body.innerHTML = `<div class="ph-rs">` + kv([['เลขที่บิล', esc(b.receipt || 'ไม่มีเลขที่บิล')], ['เวลา', `${_msDayTh(b.at)} ${_msHm(b.at)}`], ['ยอดบิล', '฿' + fmtN(b.amount)],
      ['เหตุผล', esc(b.reason)], ['ผู้ยกเลิก', esc(b.user)], ['ส่วนลด', b.discount > 0 ? '฿' + fmtN(b.discount) : ''],
      ['บิลใหม่', b.zero ? 'บิลเปล่า ฿0 · ไม่นับในการเตือน' : b.reissued_as ? 'ออกใหม่แล้ว → ' + esc(b.reissued_no || b.reissued_as) : '<b style="color:var(--bad-ink)">ไม่ออกใหม่</b>']])
      + `<p class="ms-vd-note">บิลที่ยกเลิกไม่มีรายการอาหาร ระบบ POS ไม่ส่งรายการของบิลที่ยกเลิก</p></div>`;
    return;
  }
  body.innerHTML = '<div class="ph-rs">' + uiSkel(4) + '</div>';
  let d;
  try { d = await api(`${API}/api/reports/voids/bill?${new URLSearchParams({ branch: j.branch, receipt: b.receipt })}`); }
  catch (e) { body.innerHTML = '<div class="ph-rs">' + uiEmpty({ t: 'โหลดไม่สำเร็จ' }) + '</div>'; return; }
  if (!d || !d.available) { body.innerHTML = '<div class="ph-rs">' + uiEmpty({ t: (d && d.line) || 'ไม่พบบิลนี้' }) + '</div>'; return; }
  const lines = (d.lines || []).map(l => `<div class="ph-rs-row"><dt>${esc(l.menu)} × ${fmtN(l.qty)}</dt><dd>฿${fmtN(l.rev)}</dd></div>`).join('');
  const pays = (d.payments || []).map(p => `<div class="ph-rs-row"><dt>${esc(p.code)}</dt><dd>฿${fmtN(p.amount)}</dd></div>`).join('');
  body.innerHTML = `<div class="ph-rs">` + kv([['ยอดบิล', '฿' + fmtN(d.total)], ['ส่วนลด', '฿' + fmtN(d.discount)], ['พิมพ์', fmtN(d.prints) + ' ครั้ง'],
    ['แคชเชียร์', esc(d.cashier)], ['โต๊ะ', esc(d.table)], ['ลูกค้า', d.covers ? fmtN(d.covers) + ' คน' : '']])
    + `<div class="ms-section-title ms-vd-grp">รายการอาหาร</div>` + (lines ? `<dl class="ph-rs-list">${lines}</dl>` : `<p class="ms-vd-note">ไม่มีรายการอาหารของบิลนี้ในระบบ</p>`)
    + (pays ? `<div class="ms-section-title ms-vd-grp">การชำระเงิน</div><dl class="ph-rs-list">${pays}</dl>` : '') + '</div>';
}
// outside the phone shell (the plain mobile summary) the drill level still follows back / forward
window.addEventListener('popstate', e => {
  const st = e.state || {};
  if (st.phd !== 'voids' || (phOn() && _phKind() === 'centre') || _msView !== 'voids') return;
  _msVoidFromState(st); msLoadVoids();
});
const _MS_DP = { Lunch: 'กลางวัน (11–14)', Dinner: 'เย็น (17–22)', Other: 'อื่นๆ' };
const _MS_DOW_EN = { Mon: 'จ.', Tue: 'อ.', Wed: 'พ.', Thu: 'พฤ.', Fri: 'ศ.', Sat: 'ส.', Sun: 'อา.' };
const _hh = h => String(h).padStart(2, '0') + ':00';
// peak hours (columns by hour), dayparts, weekday averages
function msPeakHtml(PK) {
  const hrs = ((PK || {}).hourly || []).filter(h => (h.revenue || 0) > 0);
  if (!hrs.length) return _msNone('ไม่มีข้อมูล');
  const top = hrs.slice().sort((a, b) => b.revenue - a.revenue).slice(0, 3).map(h => _hh(h.hour)).join(', ');
  const cols = msColumns(hrs.map(h => h.revenue), { labels: hrs.map(h => String(h.hour).padStart(2, '0')), fmt, height: 90, aria: 'รายได้รายชั่วโมง' });
  const dp = (PK.dayparts || []).map(d => `<div class="ms-row" style="padding:5px 0"><span style="width:104px;font-size:13px">${_MS_DP[d.name] || esc(d.name)}</span>${msBar(Math.round(d.pct || 0), '#0891b2')}<span style="width:38px;text-align:right;font-size:13px;font-variant-numeric:tabular-nums">${d.pct != null ? d.pct.toFixed(0) + '%' : '—'}</span></div>`).join('');
  const bd = PK.by_day || [], maxD = Math.max(1, ...bd.map(d => d.avg_revenue || 0));
  const days = bd.length ? `<p style="font-size:13px;${_msMuted};margin:8px 0 2px">รายได้เฉลี่ยต่อวัน ตามวันในสัปดาห์${PK.best_day ? ` · ดีสุด <b>${_MS_DOW_EN[PK.best_day] || PK.best_day}</b>` : ''}</p>`
    + bd.map(d => `<div class="ms-row" style="padding:4px 0"><span style="width:34px;font-size:13px;font-weight:${d.dow === PK.best_day ? 700 : 400}">${_MS_DOW_EN[d.dow] || d.dow}</span>${msBar(Math.round(100 * (d.avg_revenue || 0) / maxD), d.dow === PK.best_day ? '#4a70b0' : '#c9d5e8')}<span style="width:60px;text-align:right;font-size:13px;font-variant-numeric:tabular-nums">${fmt(d.avg_revenue || 0)}</span></div>`).join('') : '';
  return `<div style="padding-top:8px">${msGlanceLine(`พีค <b>${top}</b>${PK.peak_hour ? ` · ชั่วโมงทำเงินสูงสุด ${_hh(PK.peak_hour.hour)} (${fmt(PK.peak_hour.revenue)})` : ''}`)}</div>${cols}<p style="font-size:13px;${_msMuted};margin:2px 0 6px">แกนล่าง = ชั่วโมงของวัน</p>${dp}${days}`;
}
// tender mix (pos_payment_day) with the share change vs the comparison period
function msPaymentHtml(PY) {
  if (!PY || !PY.available || !(PY.channels || []).length) return _msNone('ไม่มีข้อมูลช่องทางชำระเงินช่วงนี้');
  const ch = PY.channels.slice(0, 7), pv = (PY.prev || {}).channels || {};
  return `<p style="font-size:13px;${_msMuted};margin:8px 0 2px">รวม ${fmt(PY.total)} · ${PY.channels.length} ช่องทาง · ตัวเลขท้าย = สัดส่วนเปลี่ยนจากเดือนก่อน</p>` + ch.map(c => {
    const p = pv[c.code], d = (p && p.pct != null) ? Math.round((c.pct - p.pct) * 10) / 10 : null;
    return `<div class="ms-row" style="padding:5px 0"><span style="width:88px;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(c.code)}</span>${msBar(Math.round(c.pct), '#0d9488')}<span style="width:44px;text-align:right;font-size:13px;font-variant-numeric:tabular-nums">${c.pct.toFixed(1)}%</span><span style="width:48px;text-align:right;font-size:13px;${_msMuted}">${d != null && d !== 0 ? (d > 0 ? '+' : '') + d + 'pp' : ''}</span></div>`;
  }).join('');
}
// GP bridge: the three effects as signed bars
function msBridgeHtml(B, title) {
  if (!B) return '';
  const rows = [['จำนวนลูกค้า', B.volume, `${fmtN(B.covers_prev)} → ${fmtN(B.covers_cur)} หัว`],
                ['ยอดต่อหัว', B.spend_head, `฿${B.spend_head_prev.toFixed(0)} → ฿${B.spend_head_cur.toFixed(0)}`],
                ['ต้นทุนต่อหัว', B.cost_head, `฿${B.cost_head_prev.toFixed(0)} → ฿${B.cost_head_cur.toFixed(0)}`]];
  const mx = Math.max(1, ...rows.map(r => Math.abs(r[1])));
  return `<div style="padding:8px 0 2px;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap"><span style="font-size:13px;font-weight:700">${title}</span><span style="font-size:20px;font-weight:800;color:${textInk(B.delta >= 0 ? '#10b981' : '#ef4444')}">${_msSM(B.delta)}</span><span style="font-size:13px;${_msMuted}">GP ${fmt(B.gp_prev)} → ${fmt(B.gp_cur)}${B.gp_pct_prev != null ? ` · ${B.gp_pct_prev.toFixed(1)}% → ${B.gp_pct_cur.toFixed(1)}%` : ''}</span></div>`
    + rows.map(([l, v, s]) => `<div class="ms-row" style="padding:5px 0"><span style="width:100px;min-width:0"><span style="font-size:13px;display:block">${l}</span><span style="font-size:13px;${_msMuted}">${s}</span></span>${msBar(Math.round(100 * Math.abs(v) / mx), v >= 0 ? '#10b981' : '#ef4444')}<span style="width:64px;text-align:right;font-size:13px;font-weight:600;font-variant-numeric:tabular-nums;color:${textInk(v >= 0 ? '#10b981' : '#ef4444')}">${_msSM(v)}</span></div>`).join('');
}
// buffet price tiers: guests share, food cost/head, food cost %
function msTiersHtml(j) {
  const T = j.tier || {}, TR = T.rows || [];
  if (!TR.length) return _msNone('ไม่มีข้อมูลกลุ่มราคาช่วงนี้');
  return TR.map(t => `<div class="ms-row" style="padding:5px 0"><span style="width:88px;min-width:0"><span style="font-size:13px;font-weight:600;display:block">${esc(t.label)}</span><span style="font-size:13px;${_msMuted}">${fmtN(t.pax)} หัว${t.d_pax_pct != null ? ` (${t.d_pax_pct > 0 ? '+' : ''}${t.d_pax_pct}%)` : ''}</span></span>${msBar(Math.round(t.share || 0), '#b45309')}<span style="width:34px;text-align:right;font-size:13px">${(t.share || 0).toFixed(0)}%</span><span style="width:70px;text-align:right;font-size:13px;font-variant-numeric:tabular-nums;line-height:1.25"><b>฿${(t.cogs_head || 0).toFixed(0)}</b>/หัว<br><span style="color:${textInk(_msCpCol(t.food_pct))}">${t.food_pct != null ? t.food_pct.toFixed(0) + '%' : '—'}</span></span></div>`).join('')
    + `<p style="font-size:13px;${_msMuted};margin:6px 0 4px">แถบ = สัดส่วนลูกค้า · ฿/หัว = ต้นทุนอาหาร · % = food cost ของกลุ่มราคา${T.mix_effect != null ? ` · ผลจากการย้ายกลุ่มราคาต่อยอด/หัว ${T.mix_effect > 0 ? '+' : ''}฿${T.mix_effect}` : ''}</p>`;
}
// discount leak cards (bills, baht, change, top branches)
function msLeakCards(L) {
  const card = (title, o, hint) => {
    o = o || {}; if (o.unavailable) return '';
    const bad = (o.bills || 0) > 0, c = bad ? '#ef4444' : '#10b981';
    const top = (o.by_branch || []).slice(0, 2).map(x => esc(x.branch)).join(', ');
    return `<div class="ms-kpi" style="border:1px solid ${c}"><p class="lbl">${title}</p><p class="val" style="font-size:20px;color:${textInk(c)}">${fmtN(o.bills || 0)} <span style="font-size:13px;font-weight:400;${_msMuted}">บิล</span></p><p class="sub" style="font-size:13px">${fmt(o.baht || 0)}${o.bills_wow != null ? ' ' + msPchip(o.bills_wow, false) : ''}</p>${top ? `<p style="font-size:13px;${_msMuted}">${top}</p>` : ''}<p style="font-size:13px;${_msMuted}">${hint}</p></div>`;
  };
  return card('ลดหนัก ≥30%', L.heavy_discount, 'ลดตั้งแต่ 30% ของมูลค่าบิล') + card('ลดแล้วจ่ายเงินสด', L.cash_heavy, 'สัญญาณเสี่ยงเงินขาด')
    + card('บิลพิมพ์ซ้ำ', L.reprinted, 'บิลส่วนลดที่พิมพ์ >1 ครั้ง') + card('บิลยอดศูนย์', L.zero_gross, 'เหลือ ฿0 หลังลด');
}

// ── VIEW: สรุป ──
function msRenderSummary() {
  const j = _msRpt; if (!j) return;
  // 2026-10-29 (owner: "ถ้าอะไรยังไม่ได้ทำก็ไม่เอาขึ้น"): a section with nothing to show is left out - no target set, no to-do -
  // and one section that fails to draw never blanks the ones after it
  const sect = (id, fn) => {
    const el = document.getElementById(id), wrap = el && el.closest('.ms-section'); if (!el) return;
    let h = '';
    try { h = fn(); } catch (e) { console.error('summary ' + id, e); }
    el.innerHTML = h || '';
    if (wrap) wrap.style.display = h ? '' : 'none';
  };
  sect('msKpis', () => msKpisHtml(j));
  sect('msTarget', () => (j.pace || {}).target ? msTargetHtml(j) : '');
  sect('msActions', () => (j.actions || []).length ? msActionsHtml(j) : '');
  try { msRenderWatch(j); } catch (e) { console.error('summary msWatch', e); }
  // DV6 (review §3.3): โตสุด / ตกสุด by the baht against last month (rev_delta), not the % - a small branch's −25 % is not the
  // branch that lost the most money; the % stays beside the baht
  const brs = j.branches || [], withMom = brs.filter(b => b.rev_delta != null);
  const best = withMom.slice().sort((a, b) => b.rev_delta - a.rev_delta)[0];
  const worst = withMom.slice().sort((a, b) => a.rev_delta - b.rev_delta)[0];
  const bwCard = (b, good) => !b ? '' : `<div class="ms-kpi" style="border:1px solid ${good ? 'var(--good-ink)' : 'var(--bad-ink)'}" data-br="${esc(b.branch)}" onclick="msOpenBranch(this.dataset.br)">
      <p class="lbl">${good ? 'เพิ่มมากสุด (บาท)' : '⚠ ลดมากสุด (บาท)'}</p>
      <p class="val" style="font-size:17px;color:var(--text-primary,#1e293b)">${esc(b.branch)} <span style="font-size:13px;${_msMuted}">›</span></p>
      <p class="sub"><b style="color:${b.rev_delta < 0 ? 'var(--bad-ink)' : 'var(--good-ink)'};font-variant-numeric:tabular-nums">${dvBaht(b.rev_delta)}</b> ${msPchip(b.wow_pct)}</p></div>`;
  const bw = document.getElementById('msBestWorst');
  if (bw) { const h = brs.length > 1 ? bwCard(best, true) + bwCard(worst, false) : ''; bw.innerHTML = h; bw.parentElement.style.display = h ? '' : 'none'; }
  msLoadYesterday();
  msPushInit();
}

// ── เมื่อวาน: the Daily Brief's first card on the phone. The last finished day's
// revenue against its normal (the median of the same weekday over 8 weeks, rules.daily_baseline),
// its customers and bills, and the branches below their own normal — from the same /api/home/brief and
// /api/home/branch-scorecard the desktop reads, so the two never disagree. Tap →
// the worst branch's sheet (the chosen branch's own sheet when one is picked).
// Hidden when the reader picked an older month: that month has no "yesterday". ──
const _msBriefCache = {};
let _msScorecard = null;
async function msLoadYesterday() {
  const wrap = document.getElementById('msYesterdayWrap'), el = document.getElementById('msYesterday');
  if (!wrap || !el) return;
  if (msPickedOlderMonth()) { wrap.style.display = 'none'; return; }
  const br = msBranch();
  try {
    if (!_msBriefCache[br]) _msBriefCache[br] = api(`${API}/api/home/brief${br ? '?branch=' + encodeURIComponent(br) : ''}`);
    if (!_msScorecard) _msScorecard = api(`${API}/api/home/branch-scorecard`);
    const [brief, sc] = await Promise.all([_msBriefCache[br], _msScorecard]);
    if (br !== msBranch() || _msView !== 'summary') return;   // the reader moved on while this loaded
    if (!brief || !brief.yesterday) { wrap.style.display = 'none'; return; }
    el.innerHTML = msYesterdayHtml(brief, sc, br);
    const ls = document.getElementById('msYestList');
    if (ls) { const h = br ? '' : msYestListHtml(sc); ls.innerHTML = h; ls.hidden = !h; }
    wrap.style.display = '';
  } catch (e) {
    delete _msBriefCache[br]; _msScorecard = null;   // a failed call is not kept for next time
    wrap.style.display = 'none';
  }
}
// phone B · the detail "เมื่อวาน": every branch of the scorecard as a two-line row, the lowest against its own normal first; a branch whose
// bills have not arrived sits last, grey (PR-PD no. 4: it is not a branch to call)
function msYestListHtml(sc) {
  const rows = ((sc || {}).branches || []).slice();
  if (rows.length < 2) return '';
  const k = b => b.reported === false ? 1e9 : b.vs_normal_pct == null ? 1e8 : b.vs_normal_pct;
  rows.sort((a, b) => k(a) - k(b));
  return rows.map(b => {
    const miss = b.reported === false, v = b.vs_normal_pct, down = !!b.below_normal;
    const why = _phCauseTxt(b).replace(/^\(|\)$/g, '');
    return msRow2({ tone: miss || v == null ? '' : down ? 'bad' : 'good', title: esc(b.branch), val: miss ? '' : '฿' + fmt(b.revenue || 0),
      fact: miss ? 'ข้อมูลยังไม่มา' : `${v != null ? msPchip(Math.round(v)) + ' ' : ''}${normalLbl(b.normal_weeks, true)}${why ? ' · ' + esc(why) : ''}`, branch: b.branch });
  }).join('');
}
function msYesterdayHtml(brief, sc, br) {
  const rev = brief.revenue_yesterday || 0, vsN = brief.revenue_vs_normal_pct;
  const d = new Date(brief.yesterday + 'T00:00:00');
  const when = isNaN(d) ? brief.yesterday : `${_MS_TH_DOW[d.getDay()]} ${d.getDate()} ${_MS_TH_MON[d.getMonth()]}`;
  const rows = ((sc || {}).branches || []);
  // the scorecard's one verdict (below_normal: 8 % or more under the median of the same weekday over 8 weeks);
  // PR-PD no. 4: a branch whose bills have not arrived (reported: false) is not "down"
  const down = _phDownRows(sc);
  const miss = (sc && sc.missing) || rows.filter(b => b.reported === false).map(b => b.branch);
  const bad = 'color:var(--bad-ink,#b91c1c)', good = 'color:var(--good-ink,#047857)';
  let line3 = '', go = '', target = '';
  if (br) {
    const me = rows.find(b => b.branch === br);
    if (me && me.vs_normal_pct != null) {
      line3 = me.below_normal
        ? `<span style="${bad}">ต่ำกว่าปกติของสาขา ${Math.abs(me.vs_normal_pct).toFixed(0)}%</span>`
        : `<span style="${good}">อยู่ในเกณฑ์ปกติของสาขา (${me.vs_normal_pct >= 0 ? '+' : ''}${me.vs_normal_pct.toFixed(0)}% ${normalLbl(me.normal_weeks, true)})</span>`;
    }
    go = 'msOpenBranch(this.dataset.br)'; target = br;
  } else {
    // 2026-10-31 (review no. 12, one fact once): the count of branches below normal is the phone hero's; the list right
    // below this card (msYestListHtml) is where every branch lives, so this card repeats neither
    if (down.length) { go = 'msOpenBranch(this.dataset.br)'; target = down[0].branch; }
    else go = "msSwitchView('branches')";
  }
  let skipNote = '';
  if (brief.skipped_day) {
    const sd = new Date(brief.skipped_day + 'T00:00:00');
    const sWhen = isNaN(sd) ? brief.skipped_day : `${sd.getDate()} ${_MS_TH_MON[sd.getMonth()]}`;
    skipNote = `<p style="font-size:13px;${_msMuted};margin-top:4px">ข้อมูลวันที่ ${esc(sWhen)} มีเพียง ${fmtN(brief.skipped_bills || 0)} บิล ยังมาไม่ครบ จึงแสดงวันก่อนหน้า</p>`;
  }
  if (!br && miss.length) skipNote += `<p style="font-size:13px;color:var(--amber-ink);margin-top:4px">ข้อมูลยังไม่มา ${fmtN(miss.length)} สาขา: ${esc(miss.slice(0, 4).join(' · '))}${miss.length > 4 ? ` +${miss.length - 4}` : ''} — ไม่นับเป็นสาขาต่ำกว่าปกติ</p>`;
  return `<div data-br="${esc(target)}" onclick="${go}" role="button" aria-label="เมื่อวาน — แตะเพื่อดูรายสาขา" style="cursor:pointer;min-height:44px">
    <p style="font-size:13px;${_msMuted}">${brief.skipped_day ? 'วันล่าสุดที่ข้อมูลครบ' : 'เมื่อวาน'} · ${esc(when)}${br ? ' · ' + esc(br) : ''}</p>
    <p style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:2px"><b style="font-size:24px;line-height:1.1">${fmt(rev)}</b>${vsN != null ? msPchip(vsN) : ''}<span style="font-size:13px;${_msMuted}">${normalLbl(brief.normal_weeks, true)}</span></p>
    <p style="font-size:13px;${_msMuted};margin-top:2px">ลูกค้า ${fmtN(brief.customer_yesterday || 0)} · ${fmtN(brief.tx_yesterday || 0)} บิล · ${fmt(brief.avg_ticket_yesterday || 0)}/บิล</p>
    ${line3 ? `<p style="font-size:13px;margin-top:6px;display:flex;align-items:center;gap:6px"><span style="flex:1;min-width:0">${line3}</span><span style="${_msMuted}">›</span></p>` : ''}
    ${skipNote}
  </div>`;
}

// ── VIEW: สาขา ──
// DV6 (phone table review 2026-10-25, §3.4 สาขา): the list opens on the baht against last month (rev_delta, the biggest loss
// first - the branch to call); เป้า ranks by the baht away from the target on the month-end projection (target_gap)
function msSetBranchSort(s) {
  _msBranchSort = s;
  ['chg', 'rev', 'rph', 'tgt'].forEach(x => document.getElementById('msSort' + x[0].toUpperCase() + x.slice(1))?.classList.toggle('ms-seg-on', x === s));
  msRenderBranches();
}
function msRenderBranches() {
  const j = _msRpt; if (!j) return;
  let branches = (j.branches || []).slice();
  const hasTgt = branches.some(b => b.vs_target_pct != null), yoy = _msYoyOk(j);
  const hasChg = branches.some(b => b.rev_delta != null);
  const tgtBtn = document.getElementById('msSortTgt'); if (tgtBtn) tgtBtn.style.display = hasTgt ? '' : 'none';
  const chgBtn = document.getElementById('msSortChg'); if (chgBtn) chgBtn.style.display = hasChg ? '' : 'none';
  if ((_msBranchSort === 'tgt' && !hasTgt) || (_msBranchSort === 'chg' && !hasChg)) _msBranchSort = 'rev';
  ['chg', 'rev', 'rph', 'tgt'].forEach(x => document.getElementById('msSort' + x[0].toUpperCase() + x.slice(1))?.classList.toggle('ms-seg-on', x === _msBranchSort));
  const S = _msBranchSort;
  const gapOf = b => b.target_gap != null ? b.target_gap : (b.target != null ? (b.projected ?? b.revenue ?? 0) - b.target : null);
  if (S === 'rev')      branches.sort((a, b) => (b.revenue || 0) - (a.revenue || 0));
  else if (S === 'chg') branches.sort((a, b) => (a.rev_delta ?? 1e15) - (b.rev_delta ?? 1e15));
  else if (S === 'rph') branches.sort((a, b) => (b.rev_per_head ?? -1e9) - (a.rev_per_head ?? -1e9));
  else                  branches.sort((a, b) => (gapOf(a) ?? 1e15) - (gapOf(b) ?? 1e15));
  const maxRev = Math.max(1, ...branches.map(b => b.revenue || 0));
  const revs = branches.map(b => b.revenue).filter(v => v != null);
  const avgW = revs.length >= 3 ? Math.round(100 * revs.reduce((a, v) => a + v, 0) / revs.length / maxRev) : null;
  const metric = S === 'chg' ? { v: b => b.rev_delta, o: { fmt: v => dvBaht(v) } }
               : S === 'rph' ? { v: b => b.rev_per_head || null, o: { fmt: v => fmt(v) + '/หัว', below: true } }
               : S === 'tgt' ? { v: b => gapOf(b), o: { fmt: v => dvBaht(v) + ' จากเป้า' } }
               : { v: b => b.revenue || null, o: { fmt, below: true } };   // 0 = not trading (e.g. a closed branch), not "the lowest"
  // the baht views open on the sum each way: "ลดลง 18 สาขา −฿4.20M · เพิ่มขึ้น 10 สาขา +฿1.10M"
  const split = vals => {
    const dn = vals.filter(v => v < 0), up = vals.filter(v => v > 0), sum = a => a.reduce((s, v) => s + v, 0);
    return [dn.length ? `${S === 'tgt' ? 'ต่ำกว่าเป้า' : 'ลดลง'} ${_glanceB(`${dn.length} สาขา ${dvBaht(sum(dn))}`)}` : '',
            up.length ? `${S === 'tgt' ? 'ถึงเป้า' : 'เพิ่มขึ้น'} ${_glanceB(`${up.length} สาขา ${dvBaht(sum(up))}`)}` : ''].filter(Boolean).join(' · ');
  };
  const sentence = branches.length < 2 ? ''
    : (S === 'chg' || S === 'tgt') ? split(branches.map(metric.v).filter(v => v != null))
    : glanceRangeSentence(branches.map(b => b.branch), branches.map(b => metric.v(b) ?? null), metric.o);
  const withT = branches.filter(b => b.vs_target_pct != null), onT = withT.filter(b => b.vs_target_pct >= 100).length;
  const head = (sentence ? `<div style="padding-top:8px">${msGlanceLine(sentence)}</div>` : '')
    + `<p style="font-size:13px;${_msMuted};margin:0 0 6px">${hasTgt ? `<b style="color:${textInk(onT >= withT.length / 2 ? '#10b981' : '#f59e0b')}">${onT}/${withT.length}</b> สาขาถึงเป้า${withT.some(b => b.target_basis === 'projected') ? ' (ประมาณการสิ้นเดือน)' : ''} · ` : ''}แตะชื่อสาขาเพื่อดูรายละเอียด${(j.headline || {}).mom_basis === 'to_date' ? ' · % เทียบวันเดียวกันของเดือนก่อน' : ''}</p>`;
  // phone review item 3 (owner 2026-10-31): one two-line row per branch (uiRow2) - line 1 the branch and the ONE figure of the chosen order; line 2
  // the change chip, one fact (target % / last year) and a thin bar of its revenue; the first 10 rows, the rest behind "ดูทั้งหมด (N)"
  const pctChip = (v, sfx) => v == null ? null : { t: `${v > 0 ? '▲' : v < 0 ? '▼' : '±'}${Math.abs(v).toFixed(Math.abs(v) >= 10 ? 0 : 1)}%${sfx || ''}`, tone: v === 0 ? '' : v > 0 ? 'good' : 'bad' };
  const nB = branches.length;
  document.getElementById('msBranches').innerHTML = head + branches.map((b, i) => {
    const g = gapOf(b);
    const val = S === 'rph' ? (b.rev_per_head != null ? fmt(b.rev_per_head) + '/หัว' : '—')
      : S === 'tgt' ? (g != null ? dvBaht(g) : '—')
      : S === 'chg' ? (b.rev_delta != null ? dvBaht(b.rev_delta) : '—') : fmt(b.revenue);
    const vt = S === 'tgt' ? (g == null ? '' : g < 0 ? 'bad' : 'good') : S === 'chg' ? (b.rev_delta == null ? '' : b.rev_delta < 0 ? 'bad' : 'good') : '';
    const tgtTone = b.vs_target_pct == null ? '' : b.vs_target_pct >= 100 ? 'good' : b.vs_target_pct >= 90 ? 'watch' : 'bad';
    const chip = S === 'tgt' ? (b.vs_target_pct != null ? { t: `${b.vs_target_pct.toFixed(0)}%`, tone: tgtTone } : null) : pctChip(b.wow_pct);
    const fact = [S === 'chg' || S === 'tgt' ? fmt(b.revenue) : '', b.vs_target_pct != null && S !== 'tgt' ? `เป้า ${b.vs_target_pct.toFixed(0)}%` : '',
                  yoy && b.yoy_pct != null ? `ปีก่อน ${b.yoy_pct > 0 ? '+' : ''}${b.yoy_pct}%` : ''].filter(Boolean).slice(0, 2).join(' · ');
    return uiRow2({ branch: b.branch, title: esc(b.branch), val, valTone: vt, tone: S === 'tgt' ? tgtTone : vt, chip, fact,
      bar: { pct: 100 * (b.revenue || 0) / maxRev, col: '#4a70b0' }, attrs: dvMoreAttr('msbr', i, nB, 10) });
  }).join('') + dvMoreBtn('msbr', nB, 10) || '<div class="ms-row">ไม่มีข้อมูล</div>';
  if (yoy && j.branches_ly_hidden) document.getElementById('msBranches').insertAdjacentHTML('beforeend', closedLyNote(j.branches_ly_hidden));   // owner 2026-10-26
}

// ── VIEW: ยอดขาย (trend by day / week / month, then peak hours and tender mix) ──
function msSetSalesGran(g) {
  _msSalesGran = g;
  ['day', 'week', 'month'].forEach(x => document.getElementById('msGran' + x[0].toUpperCase() + x.slice(1))?.classList.toggle('ms-seg-on', g === x));
  msLoadSales();
}
// ── ยอดขาย › รายเดือน: the last 12 closed months and the month in progress side by side, each with
// its change on the previous month (owner request 2026-10-12). ONE call,
// /api/reports/management/months, built from the management report's own functions, so a month
// reads here exactly as it does in the report. The month in progress is month-to-date and is
// compared on the report's weekday-fair baseline, never against a whole month. Tap a month → the
// rest of the summary switches to it (the month picker above). ──
const _msMonthsCache = {};
async function msFetchMonths(branch) {
  const key = branch || '';
  if (_msMonthsCache[key]) return _msMonthsCache[key];
  const q = new URLSearchParams({ lang: 'th' });
  if (branch) q.set('branch', branch);
  const d = await api(`${API}/api/reports/management/months?${q}`);
  if (d && d.available) _msMonthsCache[key] = d;
  return d;
}
// a change as inked text (theme good / bad inks, no chip): ▲ 5.7%  /  ▼ 2.1 pp
function msMomTxt(v, goodUp = true, unit = '%') {
  if (v == null) return `<span style="${_msMuted}">—</span>`;
  if (v === 0) return `<span style="${_msMuted}">0${unit === '%' ? '%' : ' ' + unit}</span>`;
  const good = goodUp ? v > 0 : v < 0;
  return `<span style="color:var(${good ? '--good-ink' : '--bad-ink'});font-weight:600">${v > 0 ? '▲' : '▼'} ${Math.abs(v).toFixed(1)}${unit === '%' ? '%' : ' ' + unit}</span>`;
}
const _msMomBasisNote = m => !m.mtd ? ''
  : (m.mom.basis === 'to_date' || m.mom.basis === 'same_days') ? 'เทียบวันที่ 1 ถึงวันเดียวกันของเดือนก่อน' : '';
function msMonthCell(label, value, mom, yoy) {
  return `<div style="min-width:0"><div style="font-size:13px;${_msMuted}">${label}</div>
    <div style="font-size:15px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--text-primary,#1e293b);white-space:nowrap">${value}</div>
    <div style="font-size:13px;white-space:nowrap">${mom}</div>${yoy != null ? `<div style="font-size:13px;line-height:1.3;margin-top:2px"><div style="${_msMuted};font-size:12px">ปีก่อน</div><div style="white-space:nowrap">${yoy}</div></div>` : ''}</div>`;   // 2026-10-29: the label on its own line - one 4-column cell is ~80 px, "เทียบปีก่อน ▲ 21.9%" ran into the next cell
}
function msMonthsHtml(d, selected) {
  const asc = d.months || [];
  if (asc.length < 2) return '<div class="ms-row">ยังไม่มีข้อมูลหลายเดือน</div>';
  const showGp = _msMod('cost.analysis') || _msMod('profit.pnl');
  const part = asc.findIndex(m => m.mtd);
  const labels = asc.map(m => msPeriodLabel(m.range[0], 'month'));
  const vals = asc.map(m => m.revenue);
  const [py, pm] = _msYmd(d.through || ''), dim = pm ? new Date(py, pm, 0).getDate() : 0;
  const sentence = glanceTrendSentence(labels, vals, {
    partial: part, note: part >= 0 && dim ? `${_msYmd(d.through)[2]}/${dim} วัน` : '', unit: 'เดือน', fmt });
  const under = asc.map(m => {
    const v = m.mom.revenue;
    if (v == null) return { t: '—' };
    const r = Math.round(v), t = (r > 0 ? '+' : r < 0 ? '−' : '') + Math.min(999, Math.abs(r));
    return { t, c: r === 0 ? 'var(--text-muted,#94a3b8)' : `var(${r > 0 ? '--good-ink' : '--bad-ink'})` };
  });
  const chart = msColumns(vals, {
    partial: part, labels: asc.map(m => _MS_TH_MON[+m.month.slice(5) - 1].replace(/\.$/, '')), allLabels: true,
    under, fmt, grain: 'month', height: 138, aria: 'รายได้รายเดือน และ % เทียบเดือนก่อน' });
  const cards = asc.slice().reverse().map(m => {
    const on = m.month === selected, M = m.mom, Y = m.yoy;
    const tag = m.mtd ? ` <span class="ms-chip" style="padding:1px 8px;font-size:13px;background:var(--bg-subtle);color:var(--text-secondary,#475569)">สะสม ถึงวันที่ ${_msYmd(m.through)[2]}</span>` : '';
    const note = _msMomBasisNote(m);
    const gpCell = showGp ? msMonthCell('GP %', m.gp_pct != null ? m.gp_pct.toFixed(1) + '%' : '—', m.gp_pct != null ? msMomTxt(M.gp_pp, true, 'pp') : `<span style="${_msMuted}">ไม่มีต้นทุน</span>`, null) : '';
    return `<div class="ms-mrow" role="button" tabindex="0" data-m="${esc(m.month)}" onclick="msPickMonth(this.dataset.m)" onkeydown="if(event.key==='Enter')msPickMonth(this.dataset.m)"
        style="padding:10px 0 12px;min-height:44px;cursor:pointer;border-top:1px solid var(--border,#e2e8f0);${on ? 'box-shadow:inset 3px 0 0 var(--group, var(--accent,#1f3a6b));padding-left:10px;margin-left:-10px;' : ''}">
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:6px"><b style="font-size:14px;color:var(--text-primary,#1e293b)">${esc(msPeriodLabel(m.range[0], 'month'))}</b>${tag}<span style="flex:1"></span><span style="font-size:13px;${_msMuted}">แตะเพื่อดูสรุป ›</span></div>
      ${note ? `<div style="font-size:13px;${_msMuted};margin:-2px 0 6px">${note}</div>` : ''}
      <div style="display:grid;grid-template-columns:repeat(${showGp ? 4 : 3},minmax(0,1fr));gap:6px">
        ${msMonthCell('รายได้', fmt(m.revenue), msMomTxt(M.revenue), Y ? msMomTxt(Y.revenue) : null)}
        ${msMonthCell('ลูกค้า', fmtN(m.customers), msMomTxt(M.customers), Y ? msMomTxt(Y.customers) : null)}
        ${msMonthCell('รายได้/หัว', m.rev_per_head != null ? fmt(m.rev_per_head) : '—', msMomTxt(M.rev_per_head), Y ? msMomTxt(Y.rev_per_head) : null)}
        ${gpCell}
      </div></div>`;
  }).join('');
  return `<div style="padding:10px 0 8px">${msGlanceLine(sentence)}${chart}</div>
    <p style="font-size:13px;${_msMuted};margin:0 0 6px">ตัวเลขใต้แท่ง และบรรทัดแรกใต้ตัวเลขในแต่ละเดือน = % เทียบเดือนก่อน · ปีก่อน = เทียบเดือนเดียวกันของปีก่อน · แท่งประ = เดือนปัจจุบัน (สะสม) เทียบวันเท่ากันของเดือนก่อน ไม่เทียบกับทั้งเดือน</p>
    ${cards}
    <p style="font-size:13px;${_msMuted};margin:6px 0 8px">รายได้ไม่รวม VAT และค่าบริการ · เทียบปีก่อน แสดงเมื่อข้อมูลปีก่อนครบช่วงเทียบ${showGp ? ' · GP ใช้ต้นทุนสูตร (BoM) เฉพาะเมนูที่มีต้นทุนแล้ว ต้นทุนจึงเป็นค่าขั้นต่ำ' : ''}</p>`;
}
async function msLoadMonths(wrap) {
  wrap.innerHTML = uiSkel(3);
  const br = msBranch();
  let d; try { d = await msFetchMonths(br); } catch (e) { wrap.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; return; }
  if (_msSalesGran !== 'month' || br !== msBranch()) return;   // the reader moved on while this loaded
  wrap.innerHTML = (d && d.available) ? msMonthsHtml(d, (_msRpt || {}).selected_month) : '<div class="ms-row">ยังไม่มีข้อมูลการขาย</div>';
}
// tap a month → the month picker takes it and the summary shows that month
async function msPickMonth(m) {
  const sel = document.getElementById('msMonthSel');
  if (!sel || !m || ![...sel.options].some(o => o.value === m)) return;
  sel.value = m;
  await loadMobileSummary(true);
  msSwitchView('summary');
}

async function msLoadSales() {
  const wrap = document.getElementById('msSalesTrend');
  if (_msSalesGran === 'month') { msLoadMonths(wrap); return; }
  wrap.innerHTML = uiSkel(3);
  const gran = _msSalesGran;
  const q = new URLSearchParams({ gran, trend: gran === 'day' ? '14' : '12' });
  const br = msBranch(); if (br) q.set('branch', br);
  if (msPickedOlderMonth()) q.set('date', _msRpt.range[1]);
  let d; try { d = await api(`${API}/api/sales/daily?${q}`); } catch (e) { wrap.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; return; }
  if (gran !== _msSalesGran) return;
  // latest day/week/month FIRST (owner request 2026-07-20) — the API returns the
  // trend oldest→newest; reverse for display so index 0 = the newest period.
  const asc = d.trend || [];
  const part = msPartial(asc, gran);
  const tr = asc.slice().reverse();
  const maxV = Math.max(1, ...tr.map(x => x.revenue || 0));
  const done = asc.filter((x, i) => i !== part.i && x.revenue != null);
  const avgW = done.length >= 3 ? Math.round(100 * done.reduce((a, x) => a + x.revenue, 0) / done.length / maxV) : null;
  const lblOf = x => gran === 'month' ? msPeriodLabel(x.start, 'month')
                   : gran === 'week' ? `${msPeriodLabel(x.start, 'day')} (จ.)`
                   : `${msPeriodLabel(x.start, 'day')} ${_MS_TH_DOW[msDow(x.start)]}`;
  const rows = tr.map((x, i) => {
    const w = Math.round(100 * (x.revenue || 0) / maxV);
    const ai = asc.length - 1 - i, inProg = ai === part.i;
    const latest = i === (part.i >= 0 ? 1 : 0);   // the newest complete period
    const wkend = gran === 'day' && [0, 6].includes(msDow(x.start));
    const tag = inProg ? ` <span class="ms-chip" style="padding:1px 6px;font-size:13px;background:var(--bg-subtle);color:var(--text-secondary,#475569)">${gran === 'day' ? 'วันนี้' : gran === 'week' ? 'สัปดาห์นี้' : 'MTD'}</span>` : '';
    return `<div class="ms-row" style="${wkend ? 'background:var(--bg-subtle);margin:0 -16px;padding-left:16px;padding-right:16px' : ''}">
      <span style="width:${gran === 'month' ? 58 : 82}px;font-size:13px;font-weight:${latest ? '700' : '400'};color:${latest ? 'var(--text-primary,#1e293b)' : 'var(--text-muted,#64748b)'}">${lblOf(x)}</span>
      ${msBar(w, inProg ? 'var(--border,#cbd5e1)' : latest ? 'var(--group, var(--accent,#1f3a6b))' : 'color-mix(in srgb, var(--group, var(--accent,#1f3a6b)) 38%, transparent)', avgW)}
      <span style="width:${inProg ? 104 : 64}px;text-align:right;font-size:13px;font-weight:${latest ? '700' : '400'};font-variant-numeric:tabular-nums;color:var(--text-primary,#1e293b)">${fmt(x.revenue || 0)}${tag}</span>
    </div>`;
  }).join('');
  // PR-PP2: the latest period (to date when it is not finished) with เทียบช่วงก่อน + เทียบปีก่อน
  const T0 = d.total || {}, R0 = periodOf(d);
  const cmp0 = R0 ? `<div class="ms-row" style="flex-wrap:wrap;gap:6px"><span style="font-size:13px;font-weight:600">${esc(R0.label_th)}</span>`
    + `<b style="font-variant-numeric:tabular-nums">${fmt(T0.revenue || 0)}</b>`
    + periodCmpHtml(T0.revenue, T0.prev && T0.prev.revenue, T0.ly && T0.ly.revenue, { range: R0 }) + `</div>` : '';
  wrap.innerHTML = rows
    ? `${cmp0}<div style="padding:10px 0 8px">${msTrendBlock(asc, gran, 110)}</div>
       <p style="font-size:13px;${_msMuted};margin:0 0 2px">ขีดในแถบ = ค่าเฉลี่ย${gran === 'day' ? ' · แถบเทา = เสาร์-อาทิตย์' : gran === 'week' ? ' · สัปดาห์ จ.–อา.' : ''}</p>${rows}`
    : '<div class="ms-row">ไม่มีข้อมูล</div>';
}
function msRenderPeakPay() {
  const j = _msRpt; if (!j) return;
  const pk = document.getElementById('msPeak'), py = document.getElementById('msPayment');
  if (pk) pk.innerHTML = msPeakHtml(j.peak);
  if (py) py.innerHTML = msPaymentHtml(j.payment);
}

// ── VIEW: เมนู ──
// DV6 (phone table review 2026-10-25, §3.4 เมนู + top-10 #9): the tab answers "which dishes push the food cost per head up, in baht"
// - the management payload's own list (menu.risers: each dish's cost per head now vs last month, the to-do line's figure; DV1 split it
// into the cost per plate and the plates per head) x the period's customers. Top 15 (owner 2026-10-29), the rest behind ดูทั้งหมด. The old มาแรง (% of
// pieces) and ขายดี (pieces) lists are gone; a role without a cost module (the server strips menu.risers, PL3) keeps ขายดี in plates.
async function msLoadMenu() {
  const j = _msRpt, wrap = document.getElementById('msMenuRisers'), title = document.getElementById('msMenuTitle');
  if (!j || !wrap) return;
  const M = j.menu || {};
  msLoadMenuTop(j);
  if (canSeeCost() && !M.hidden) {
    if (title) title.textContent = 'เมนูที่ดันต้นทุนต่อหัวขึ้น (บาท)';
    wrap.innerHTML = msCostRisersHtml(j, 'msmenu', 15);
    return;
  }
  if (title) title.textContent = 'เมนูขายดี (จำนวนจาน)';
  wrap.innerHTML = uiSkel(3);
  const q = new URLSearchParams({ sort: 'qty', limit: '20' });
  const br = msBranch(); if (br) q.set('branch', br);
  const mon = j.selected_month || ''; if (mon) q.set('month', mon);
  let d; try { d = await api(`${API}/api/sales/top_ordered?${q}`); } catch (e) { wrap.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; return; }
  if (_msRpt !== j) return;
  wrap.innerHTML = msTopMenuHtml((d.items || []).slice(0, 20));
}
// the dishes whose food cost per head rose, by the baht it added (cost per head change x the customers of the period): the context
// line first (the whole cost per head, gp.cost_per_head - the bridge's), then the rows with their share of the rise and why it rose
// the cause word of a cost riser: which of the two parts of its change carries more - the cost per plate (price_delta) or the plates
// per head (usage_delta); both when neither is under 60 % of the rise
function _msRiserCause(m) {
  const p = +m.price_delta || 0, u = +m.usage_delta || 0, tot = Math.max(p, 0) + Math.max(u, 0);
  if (!tot || m.price_delta == null || m.usage_delta == null) return null;
  // 2026-10-31 (phone review item 3): the cause is ONE short chip (≤ 6 characters) - ราคาขึ้น (cost per plate) · ตักเพิ่ม (plates per head) · ทั้งสอง
  if (p > 0 && u > 0 && p / tot < 0.6 && u / tot < 0.6) return { t: 'ทั้งสอง', tone: 'watch' };
  return p >= u ? { t: 'ราคาขึ้น', tone: 'bad' } : { t: 'ตักเพิ่ม', tone: 'watch' };
}
// phone review item 3 (owner 2026-10-31): one two-line row per dish - line 1 the dish and the baht it adds; line 2 the cause chip, the change per
// head and a thin bar of its share; the cost per plate, the plates per head and the running share are behind a tap (the row expands)
function msCostRisersHtml(j, key, top) {
  const M = j.menu || {}, G = j.gp || {}, cov = M.covers || 0;
  const rows = (M.risers || []).filter(m => (m.delta || 0) > 0);
  if (M.cost_basis === 'unavailable') return _msNone('ยังไม่มีต้นทุนรายเมนูของช่วงนี้');
  if (!rows.length || !cov) return _msNone('ไม่มีเมนูที่ต้นทุนต่อหัวเพิ่มขึ้นเทียบเดือนก่อน');
  const bahtOf = m => m.delta * cov, total = rows.reduce((s, m) => s + bahtOf(m), 0);
  const sg = v => (v > 0 ? '+' : v < 0 ? '−' : '') + '฿' + Math.abs(v).toFixed(2);
  const cph = G.cost_per_head, cph0 = G.cost_per_head_prev, B = G.bridge || {};
  const ctx = cph != null && cph0 != null
    ? ` · ต้นทุนต่อหัวทั้งหมด ฿${cph0.toFixed(0)} → ฿${cph.toFixed(0)}${B.cost_head != null ? ` (ผลต่อกำไร ${dvBaht(B.cost_head)})` : ''}` : '';
  const head = `<div style="padding-top:8px">${msGlanceLine(`${fmtN(rows.length)} เมนูเพิ่มต้นทุนรวม ${_glanceB('+฿' + fmt(total))} เทียบเดือนก่อน${ctx}`)}</div>`;
  let cum = 0;
  const body = rows.map((m, i) => {
    const b = bahtOf(m), share = total ? 100 * b / total : 0;
    cum += share;
    // PR-PD no. 18: the dish's COST per plate (never a selling price); an unchanged one is said in words - behind the tap
    const why = m.price_delta != null && m.usage_delta != null ? `${Math.abs(m.price_delta) < 0.005 ? 'ต้นทุนต่อจานเท่าเดิม' : 'ต้นทุนต่อจาน ' + sg(m.price_delta)} · กินต่อหัว ${sg(m.usage_delta)}<br>` : '';
    return uiRow2({ tone: 'bad', title: `<span class="ms-menu-name" title="${esc(m.menu)}">${i + 1}. ${esc(_msShortMenu(m.menu))}</span>`, val: `+฿${fmt(b)}`, valTone: 'bad',
      chip: _msRiserCause(m), fact: `${sg(m.delta)}/หัว · ${share.toFixed(0)}%`, bar: { pct: 100 * b / Math.max(1, ...rows.map(bahtOf)), col: '#c62828' },
      more: `${why}สัดส่วน ${share.toFixed(0)}% · สะสม ${Math.min(100, cum).toFixed(0)}%`, attrs: dvMoreAttr(key, i, rows.length, top) });
  }).join('');
  return head + body + dvMoreBtn(key, rows.length, top)
    + `<p style="font-size:13px;${_msMuted};margin:6px 0 4px">บาท = ต้นทุนต่อหัวที่เพิ่ม × ลูกค้า ${fmtN(cov)} คน</p>`;
}
// 2026-10-31 (owner) · 60 เมนูที่สั่งเยอะ: /api/sales/top_ordered?windows=1 answers three windows at once (week = the 7 finished days to the
// last finished day, half = the 15, month = the month to date; each vs the same-length window just before); chips สัปดาห์ · 15 วัน · เดือน
// switch client-side and the pick is kept for the session (as the voids card). Row: dish + plates; ต่อหัว = plates per 100 customers (a buffet
// dish on the buffet customers) with its change vs the window before - more plates per head = more food cost, so up is red.
const _MS_TW = [['week', 'สัปดาห์'], ['half', '15 วัน'], ['month', 'เดือน']];
let _msTopAns = null;
function _msTopKey(W) {
  let k = 'month';
  try { k = sessionStorage.getItem('msTopWin') || 'month'; } catch (e) { /* private mode: the default */ }
  return (W || {})[k] ? k : 'month';
}
function msSetTopWin(k) {
  try { sessionStorage.setItem('msTopWin', k); } catch (e) { /* private mode: this render only */ }
  if (_msTopAns) msRenderMenuTop(_msTopAns);
}
async function msLoadMenuTop(j) {
  const el = document.getElementById('msMenuTop');
  if (!el) return;
  el.innerHTML = uiSkel(3);
  const q = new URLSearchParams({ windows: '1' });
  const br = msBranch(); if (br) q.set('branch', br);
  const mon = j.selected_month || ''; if (mon) q.set('month', mon);
  let d; try { d = await api(`${API}/api/sales/top_ordered?${q}`); } catch (e) { el.innerHTML = _msNone('โหลดไม่สำเร็จ'); return; }
  if (_msRpt !== j) return;
  _msTopAns = d;
  msRenderMenuTop(d);
}
function msRenderMenuTop(d) {
  const el = document.getElementById('msMenuTop');
  if (!el) return;
  const W = (d || {}).windows || {}, k = _msTopKey(W), w = W[k];
  const chips = `<div class="ms-vw-chips" role="group" aria-label="ช่วงเวลาของ 60 เมนูที่สั่งเยอะ">` + _MS_TW.filter(([x]) => W[x]).map(([x, t]) =>
    `<button type="button" class="ms-vw-chip" aria-pressed="${x === k}" onclick="msSetTopWin('${x}')">${t}</button>`).join('') + '</div>';
  const items = (w || {}).items || [];
  if (!items.length) { el.innerHTML = (w ? chips : '') + _msNone('ไม่มีข้อมูลเมนูช่วงนี้'); return; }
  const p1 = v => v == null ? '—' : (+v).toLocaleString('th-TH', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const pill = v => v == null ? '' : msPchip(Math.round(v * 10) / 10, false);
  const head = msGlanceLine(`สั่งรวม ${_glanceB(fmtN(w.total_qty) + ' จาน')} · ลูกค้า ${fmtN(w.customers)} คน · ต่อหัว ${_glanceB(p1(w.per100))} จาน/100 คน`
    + (w.delta_pct != null ? ` (เทียบช่วงก่อน ${pill(w.delta_pct)})` : '') + `<br>${esc(w.label)} เทียบ ${esc(w.prev_label)}`);
  const seen = {};
  items.forEach(m => { const n = _msShortMenu(m.menu); seen[n] = (seen[n] || 0) + 1; });
  const maxQ = Math.max(1, ...items.map(m => m.qty || 0));
  const body = items.map((m, i) => {
    const nm = _msShortMenu(m.menu), isNew = !(m.prev_qty > 0);
    const kind = seen[nm] > 1 ? (m.kind === 'buffet' ? ' · บุฟเฟ่ต์' : ' · ไม่ใช่บุฟเฟ่ต์') : '';
    return uiRow2({ title: `<span class="ms-menu-name" title="${esc(m.menu)}">${i + 1}. ${esc(nm)}</span>`, val: `${fmtN(m.qty)} จาน`,
      chip: isNew ? { t: 'ใหม่', tone: 'watch' } : null, chips: isNew ? '' : pill(m.delta_pct),
      fact: `${p1(m.per100)} จาน/100 คน${kind}`, bar: { pct: 100 * (m.qty || 0) / maxQ, col: '#0891b2' },
      attrs: dvMoreAttr('mstop', i, items.length, 10) });
  }).join('');
  el.innerHTML = chips + `<div style="padding-top:6px">${head}</div>` + body + dvMoreBtn('mstop', items.length, 10)
    + `<p style="font-size:13px;${_msMuted};margin:6px 0 4px">เมนูบุฟเฟ่ต์คิดต่อลูกค้าบุฟเฟ่ต์ · ▲ แดง = กินต่อหัวมากขึ้น</p>`;
}
function msTopMenuHtml(items) {
  const maxQ = Math.max(1, ...items.map(x => x.qty || 0));
  return items.map((x, i) => {
    const nm = (x.description || x.sku_code || '').replace(/^\((?:A|B)\)\s*/, ''), mom = x.qty_mom_pct;
    return uiRow2({ title: `${i + 1}. ${esc(nm)}`, val: fmtN(x.qty || 0) + ' จาน',
      chip: mom != null ? { t: `${mom > 0 ? '▲' : mom < 0 ? '▼' : '±'}${Math.abs(mom).toFixed(0)}%`, tone: mom >= 0 ? 'good' : 'bad' } : null,
      fact: mom != null ? 'เทียบเดือนก่อน' : '', bar: { pct: 100 * (x.qty || 0) / maxQ, col: '#0891b2' } });
  }).join('') || '<div class="ms-row">ไม่มีข้อมูล</div>';
}

// ── VIEW: กำไร/ต้นทุน (GP, food cost, the bridge, price tiers, cost/head by branch) ──
function msProfitKpisHtml(j) {
  const H = j.headline || {}, P = j.pace || {}, G = j.gp || {}, M = j.menu || {};
  const fcPrev = P.food_cost_pct_prev;
  const fct = fcTargetSet(j.food_cost_target);
  // DV1: one food cost per head - the cost GP subtracts ÷ covers (gp.cost_per_head, = the bridge's), menu.total_cph only as a fallback
  const cph = G.cost_per_head != null ? G.cost_per_head : M.total_cph;
  const cphD = G.cost_per_head != null ? (G.cost_per_head_prev != null ? G.cost_per_head - G.cost_per_head_prev : null) : M.total_cph_delta;
  return [
    { l: 'GP %', v: G.gp_pct != null ? G.gp_pct.toFixed(1) + '%' : '—', c: '#10b981', s: G.gp_pct_delta_pp != null ? `${G.gp_pct_delta_pp > 0 ? '+' : ''}${G.gp_pct_delta_pp} จุด เทียบเดือนก่อน` : (G.cost_basis === 'unavailable' ? 'ไม่มีข้อมูลต้นทุน' : '') },
    { l: 'ต้นทุนอาหาร % (BoM)', v: P.food_cost_pct != null ? P.food_cost_pct.toFixed(1) + '%' : '—', c: _msCpCol(P.food_cost_pct), s: `<span class="ms-fc-badge">เป้า ${fcTargetTxt(fct)}%${P.food_cost_pct != null ? ' · ' + fcStatusText(P.food_cost_pct, fct) : ''}</span>${fcPrev != null ? ` · เดือนก่อน ${fcPrev.toFixed(1)}%` : ''}` },
    { l: 'ต้นทุนอาหาร/หัว', v: cph != null ? fmt(cph) : '—', c: '#ef4444', s: cphD != null ? `${cphD > 0 ? '+' : cphD < 0 ? '−' : ''}฿${Math.abs(cphD).toFixed(1)} vs เดือนก่อน` : '' },
    { l: 'GP/หัว', v: G.gp_per_head != null ? fmt(G.gp_per_head) : '—', c: '#0891b2', s: H.rev_per_head != null ? `รายได้/หัว ${fmt(H.rev_per_head)}` : '' },
  ].map(k => `<div class="ms-kpi"><p class="lbl">${k.l}</p><p class="val" style="font-size:20px;color:${textInk(k.c)}">${k.v}</p><p class="sub" style="font-size:13px;${_msMuted}">${k.s}</p></div>`).join('');
}
function msCoverageNote(j) {
  const cc = (j.pace || {}).cost_coverage;
  return (cc && cc.coverage_pct != null && cc.coverage_pct < 99.5)
    ? `<p style="font-size:13px;color:var(--amber-ink,#b45309);margin:4px 0 0">ต้นทุนครอบคลุม ${cc.coverage_pct}% ของจาน${cc.est_food_cost_pct != null ? ` · ประมาณการ food cost จริง ~${cc.est_food_cost_pct}%` : ''} — ตัวเลขต้นทุนจึงเป็นขั้นต่ำ</p>` : '';
}
async function msRenderProfit() {
  const j = _msRpt; if (!j) return;
  const G = j.gp || {};
  document.getElementById('msProfitKpis').innerHTML = msProfitKpisHtml(j);
  const cn = document.getElementById('msProfitNote'); if (cn) cn.innerHTML = msCoverageNote(j);
  const bEl = document.getElementById('msBridge');
  if (bEl) bEl.innerHTML = (msBridgeHtml(G.bridge, 'เทียบเดือนก่อน') + msBridgeHtml(G.bridge_yoy, 'เทียบปีก่อน')) || _msNone('ต้องมีข้อมูลต้นทุนทั้งสองช่วง');
  const tEl = document.getElementById('msTiers'); if (tEl) tEl.innerHTML = msTiersHtml(j);
  // per-branch food cost (cost.analysis) for the month on screen
  const br = document.getElementById('msProfitBranches');
  if (!br) return;
  if (!_msMod('cost.analysis')) { br.innerHTML = _msNone('สิทธิ์ของคุณไม่รวมต้นทุนรายสาขา'); return; }
  br.innerHTML = uiSkel(3);
  const q = new URLSearchParams({ gran: 'month' });
  const b = msBranch(); if (b) q.set('branch', b);
  if (j.range && j.range[1]) q.set('date', j.range[1]);   // the month on screen, not the latest data day (one branch's sliver)
  let d; try { d = await api(`${API}/api/sales/cost-per-head?${q}`); } catch (e) { br.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; return; }
  if (_msRpt !== j) return;
  const branches = (d.branches || []).filter(r => r.cost_pct != null).sort((a, b2) => b2.cost_pct - a.cost_pct);   // worst first
  const fct = fcTarget(), over = branches.filter(r => r.cost_pct > fct).length;
  const sentence = branches.length > 1 ? glanceRangeSentence(branches.map(r => r.branch), branches.map(r => r.cost_pct), { pct: true, fmt: v => v + '%' }) : '';
  br.innerHTML = (sentence ? `<div style="padding-top:8px">${msGlanceLine(`ต้นทุนอาหาร ${sentence}${over ? ` · <b style="color:var(--bad-ink)">${over} สาขาเกินเป้า ${fcTargetTxt(fct)}%</b>` : ''}`)}</div>` : '') + branches.map(r => `<div class="ms-row" data-br="${esc(r.branch)}" onclick="msOpenBranch(this.dataset.br)" style="cursor:pointer">
      <span style="flex:1;font-size:13px;color:var(--text-primary,#1e293b)">${esc(r.branch)}</span>
      <span style="font-size:13px;${_msMuted};font-variant-numeric:tabular-nums">${r.cost_per_head != null ? fmt(r.cost_per_head) : '—'}/หัว</span>
      <span class="ms-chip" style="background:${_msCpCol(r.cost_pct)}22;color:${textInk(_msCpCol(r.cost_pct))}">${r.cost_pct}%</span>
      <span style="${_msMuted}">›</span>
    </div>`).join('') || '<div class="ms-row">ไม่มีข้อมูล</div>';
}

// ── VIEW: ส่วนลด & เงิน (the money-leak radar of the report, on the phone) ──
function msRenderDiscounts() {
  const j = _msRpt; if (!j) return;
  const H = j.headline || {}, L = j.leak || {};
  const pol = H.discount_policy_pct, over = H.discount_excess;
  const c = H.discount_pct == null ? '#94a3b8' : (pol != null && H.discount_pct > pol) ? '#ef4444' : '#10b981';
  document.getElementById('msDiscKpis').innerHTML =
    `<div class="ms-kpi" style="grid-column:1/-1"><p class="lbl">ส่วนลดรวม${j.partial ? ' (สะสม)' : ''}</p><p class="val" style="color:${textInk(c)}">${fmt(H.discount || 0)}</p><p class="sub"><b style="color:${textInk(c)}">${H.discount_pct != null ? H.discount_pct.toFixed(1) + '%' : '—'}</b> <span style="${_msMuted};font-size:13px">ของยอดก่อนลด${pol != null ? ` · เพดาน ${pol}%` : ''}${over ? ` · เกินเพดาน ${fmt(over)}` : ''}</span></p></div>`;
  document.getElementById('msDiscCards').innerHTML = msLeakCards(L) || _msNone('ไม่มีข้อมูลส่วนลดช่วงนี้');
  document.getElementById('msDiscBranches').innerHTML = msDiscBranchRows(L.by_master_branch, pol, 'เฉพาะสาขาบริษัท') || _msNone('ไม่มีข้อมูลรายสาขา');
  // 2026-10-31 (owner): the franchise branches in the same layout under the company list; hidden when there is none in scope
  const fr = msDiscBranchRows(L.by_franchise_branch, pol, 'เฉพาะสาขาแฟรนไชส์'), frWrap = document.getElementById('msDiscFrWrap');
  if (frWrap) { document.getElementById('msDiscFranchise').innerHTML = fr; frWrap.style.display = fr ? '' : 'none'; }
}
// one discount-by-branch list: bar + % of the branch's sales + baht, tap = the branch sheet; '' when there is no row
function msDiscBranchRows(rows, pol, scope) {
  const mb = rows || [], maxP = Math.max(1, ...mb.map(x => x.pct || 0));
  if (!mb.length) return '';
  return mb.map(x => {
    const bad = pol != null && x.pct > pol;
    return `<div class="ms-row" data-br="${esc(x.branch)}" onclick="msOpenBranch(this.dataset.br)" style="cursor:pointer;padding:6px 0"><span style="width:88px;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(x.branch)}</span>${msBar(Math.round(100 * (x.pct || 0) / maxP), bad ? '#ef4444' : '#f59e0b')}<span style="width:40px;text-align:right;font-size:13px;font-weight:600;font-variant-numeric:tabular-nums;color:${textInk(bad ? '#ef4444' : '#475569')}">${x.pct != null ? x.pct.toFixed(1) + '%' : '—'}</span><span style="width:58px;text-align:right;font-size:13px;${_msMuted};font-variant-numeric:tabular-nums">${fmt(x.disc)}</span></div>`;
  }).join('') + `<p style="font-size:13px;${_msMuted};margin:6px 0 4px">${scope} · % = ส่วนลด ÷ ยอดขายของสาขา · แตะเพื่อดูสาขา</p>`;
}

// ── BRANCH SHEET: tap a branch anywhere → everything about it for the month ──
async function msOpenBranch(name) {
  if (!name) return;
  const sh = document.getElementById('msSheet'), body = document.getElementById('msSheetBody');
  if (!sh || !body) return;
  document.getElementById('msSheetTitle').textContent = name;
  document.getElementById('msSheetSub').textContent = (_msRpt && _msRpt.label) || '';
  sh.classList.add('open'); document.body.classList.add('ms-sheet-open'); sh.scrollTop = 0;
  body.innerHTML = '<div class="ms-section">' + uiSkel(4) + '</div>';
  const mon = msMonth();
  const dq = new URLSearchParams({ gran: 'day', trend: '14', branch: name });
  const mq = new URLSearchParams({ sort: 'qty', limit: '40', branch: name });
  if (msPickedOlderMonth()) dq.set('date', _msRpt.range[1]);
  if (_msRpt && _msRpt.selected_month) mq.set('month', _msRpt.selected_month);
  const canMenu = (_msMod('menu.analysis') || _msMod('cost.analysis')) && !canSeeCost();   // DV6: a cost role reads the menu list from the payload
  let j, days = null, menu = null;
  try {
    [j, days, menu] = await Promise.all([
      msFetchRpt(name, mon),
      api(`${API}/api/sales/daily?${dq}`).catch(() => null),
      canMenu ? api(`${API}/api/sales/top_ordered?${mq}`).catch(() => null) : Promise.resolve(null),
    ]);
  } catch (e) { body.innerHTML = '<div class="ms-section"><div class="ms-card"><div class="ms-row">โหลดไม่สำเร็จ</div></div></div>'; return; }
  if (document.getElementById('msSheetTitle').textContent !== name) return;   // another branch was opened meanwhile
  if (!j || !j.available) { body.innerHTML = '<div class="ms-section"><div class="ms-card"><div class="ms-row">ไม่มีข้อมูลของสาขานี้</div></div></div>'; return; }
  body.innerHTML = msBranchSheetHtml(j, days, menu);
}
function msCloseSheet() {
  const sh = document.getElementById('msSheet');
  if (sh) {
    const row = sh.classList.contains('ph-rowsheet');
    sh.classList.remove('open', 'ph-rowsheet');
    if (row) { const bk = sh.querySelector('.ms-header .ms-toggle'); if (bk) bk.setAttribute('aria-label', 'กลับ'); }
  }
  const back = document.getElementById('phSheetBack'); if (back) back.hidden = true;     // PR-MA: the row sheet's dim backdrop
  document.body.classList.remove('ms-sheet-open');
}
function msBranchSheetHtml(j, days, menu) {
  const sec = (title, inner, grid) => `<div class="ms-section"><div class="ms-section-title">${title}</div>${grid ? `<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">${inner}</div>` : `<div class="ms-card">${inner}</div>`}</div>`;
  const H = j.headline || {}, G = j.gp || {}, M = j.menu || {}, L = j.leak || {};
  const cost = _msMod('cost.analysis') || _msMod('profit.pnl');
  const dtr = (days || {}).trend || [];
  const trend14 = dtr.length >= 3 ? `<div style="padding:8px 0 4px">${msTrendBlock(dtr, 'day', 100)}</div>` : _msNone('ไม่มีข้อมูลรายวัน');
  const pool = (menu || {}).items || [];
  // DV6: the branch's dishes that push its cost per head up, in baht (the เมนู tab's list); a role without cost: ขายดี in plates
  const menuHtml = cost && !M.hidden ? msCostRisersHtml(j, 'mssheet', 5)
    : pool.length ? `<p style="font-size:12px;${_msMuted};margin:8px 0 0">ขายดี (จำนวนจาน)</p>${msTopMenuHtml(pool.slice(0, 8))}` : '';
  const cph = (M.top || []).slice(0, 6);
  const cphHtml = cph.length ? cph.map((m, i) => `<div class="ms-row" style="padding:5px 0"><span style="width:18px;font-size:12px;${_msMuted}">${i + 1}</span><span style="flex:1;min-width:0;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(m.menu)}</span><span style="font-size:12px;font-weight:600;font-variant-numeric:tabular-nums">฿${(m.cost_per_head || 0).toFixed(2)}</span>${m.delta != null ? `<span style="font-size:12px;width:52px;text-align:right;color:${textInk(m.delta > 0 ? '#ef4444' : m.delta < 0 ? '#10b981' : '#64748b')}">${m.delta > 0 ? '+' : ''}${m.delta.toFixed(2)}</span>` : ''}</div>`).join('') + `<p style="font-size:12px;${_msMuted};margin:6px 0 4px">ต้นทุนอาหารของเมนู ÷ จำนวนหัวของสาขา · ตัวเลขท้าย = เปลี่ยนจากเดือนก่อน${M.total_cph != null ? ` · รวม ฿${M.total_cph.toFixed(0)}/หัว` : ''}</p>` : _msNone('ไม่มีข้อมูลต้นทุนเมนู');
  const pol = H.discount_policy_pct;
  const discHtml = `<div class="ms-row"><span style="flex:1;font-size:13px">ส่วนลดรวม</span><b>${fmt(H.discount || 0)}</b><span class="ms-chip" style="background:${(pol != null && H.discount_pct > pol) ? '#fee2e2' : '#dcfce7'};color:${(pol != null && H.discount_pct > pol) ? '#b91c1c' : '#047857'}">${H.discount_pct != null ? H.discount_pct.toFixed(1) + '%' : '—'}</span></div>`
    + [['ลดหนัก ≥30%', L.heavy_discount], ['ลดแล้วจ่ายเงินสด', L.cash_heavy], ['บิลพิมพ์ซ้ำ', L.reprinted], ['บิลยอดศูนย์', L.zero_gross]].map(([l, o]) => (o && !o.unavailable) ? `<div class="ms-row" style="padding:6px 0"><span style="flex:1;font-size:12px">${l}</span><span style="font-size:12px;font-variant-numeric:tabular-nums"><b>${fmtN(o.bills || 0)}</b> บิล · ${fmt(o.baht || 0)}</span>${o.bills_wow != null ? msPchip(o.bills_wow, false) : ''}</div>` : '').join('');
  const brName = document.getElementById('msSheetTitle')?.textContent || '';
  const goBtn = (t, lbl) => `<button onclick="msSheetGo('${t}', this.dataset.br)" data-br="${esc(brName)}" style="flex:1;min-height:44px;padding:10px 12px;font-size:13px;font-weight:600;border-radius:var(--r-md);border:1px solid var(--border,#e2e8f0);background:var(--bg-card,#fff);color:var(--text-primary,#1e293b)">${lbl}</button>`;
  const go = [_msMod('ops.discounts') ? goBtn('discounts', 'ส่วนลดสาขานี้ →') : '',
              (_msMod('menu.analysis') || _msMod('cost.analysis')) ? goBtn('menu', 'เมนูสาขานี้ →') : ''].filter(Boolean);
  return `<div class="ms-section" style="margin-top:12px"><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">${msKpisHtml(j)}</div></div>`
    + (go.length ? `<div class="ms-section" style="margin-top:10px;display:flex;gap:8px">${go.join('')}</div>` : '')
    + sec('เป้าเดือนนี้', msTargetHtml(j))
    + sec('14 วันล่าสุด', trend14)
    + sec('สิ่งที่ต้องทำ', msActionsHtml(j))
    + (menuHtml ? sec(cost && !M.hidden ? 'เมนูที่ดันต้นทุนต่อหัวขึ้น (บาท)' : 'เมนู', menuHtml) : '')
    + (cost ? sec('ต้นทุน/หัว สูงสุด (รายเมนู)', cphHtml) : '')
    + (_msMod('ops.discounts') ? sec('ส่วนลด', discHtml) : '')
    + sec('ชั่วโมงพีค & ช่วงเวลา', msPeakHtml(j.peak))
    + sec('ช่องทางชำระเงิน', msPaymentHtml(j.payment))
    + sec('กลุ่มราคาบุฟเฟ่ต์', msTiersHtml(j))
    + (cost ? sec('กำไรขั้นต้น', `<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0 4px">${msProfitKpisHtml(j)}</div>${msBridgeHtml(G.bridge, 'สะพาน GP เทียบเดือนก่อน') || ''}`) : '')
    + `<div class="ms-section" style="text-align:center;margin-top:18px"><button onclick="msCloseSheet()" style="min-height:44px;padding:10px 18px;font-size:13px;font-weight:600;border-radius:var(--r-md);border:1px solid var(--border,#e2e8f0);background:var(--bg-card,#fff);color:var(--text-primary,#1e293b)">← กลับหน้ารวม</button></div>`;
}
// ── The sheet goes somewhere: ‹ › (or a swipe) steps through the branches in the
// picker's order; the jump buttons open the full dashboard on that branch's
// discounts / menu page with the branch filter already set. ──
function _msBranchNames() {
  return [...document.querySelectorAll('#msBranch option')].map(o => o.value).filter(Boolean);
}
function msSheetStep(d) {
  const names = _msBranchNames(); if (!names.length) return;
  const cur = document.getElementById('msSheetTitle')?.textContent || '';
  const i = names.indexOf(cur);
  msOpenBranch(names[((i < 0 ? 0 : i) + d + names.length) % names.length]);
}
async function msSheetGo(tab, branch) {
  msCloseSheet();
  await showFullDashboard(tab);
  const sel = document.getElementById('selBranch');
  if (sel && branch && [...sel.options].some(o => o.value === branch) && sel.value !== branch) { sel.value = branch; onFilterChange(); }
}
(function () {
  const sh = document.getElementById('msSheet'); if (!sh) return;
  let x0 = null, y0 = null;
  sh.addEventListener('touchstart', e => { const t = e.touches[0]; x0 = t.clientX; y0 = t.clientY; }, { passive: true });
  sh.addEventListener('touchend', e => {
    if (x0 == null) return;
    const t = e.changedTouches[0], dx = t.clientX - x0, dy = t.clientY - y0; x0 = y0 = null;
    if (sh.classList.contains('ph-rowsheet')) { if (dy > 80 && Math.abs(dx) < 60 && sh.scrollTop <= 0) msCloseSheet(); return; }   // PR-MA: a row sheet closes on a swipe down
    if (Math.abs(dx) > 70 && Math.abs(dy) < 50) msSheetStep(dx < 0 ? 1 : -1);
  }, { passive: true });
})();

async function loadMoreAnalytics() {
  const gen = loadGen;
  const p = new URLSearchParams();
  if (getBranch())    p.set('branch', getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to', getMonthTo());
  const q = p.toString();
  const bq = getBranch() ? ('branch=' + encodeURIComponent(getBranch())) : '';
  // Turnover is ALWAYS all-branches (comparison).
  const monthQ = new URLSearchParams();
  if (getMonthFrom()) monthQ.set('month_from', getMonthFrom());
  if (getMonthTo())   monthQ.set('month_to', getMonthTo());
  const [dp, turn, fc] = await Promise.all([
    getJSON('/api/analysis/daypart-economics?' + q, { gen }).catch(()=>null),
    getJSON('/api/analysis/table-turnover?' + monthQ.toString(), { gen }).catch(()=>null),
    getJSON('/api/forecast?mode=trend&' + bq, { gen }).catch(()=>null),
  ]);
  if (gen !== loadGen) return;

  const dayparts = (dp && dp.dayparts) || [];
  if (dp) {
    const maxPct = Math.max(1, ...dayparts.map(x=>x.revenue_pct||0));
    const maxGp = Math.max(...dayparts.map(x=>x.est_gp_margin_pct||0));
    const minGp = Math.min(...dayparts.map(x=>x.est_gp_margin_pct==null?999:x.est_gp_margin_pct));
    const gpCol = m => m==null ? '#94a3b8' : m>=55 ? '#10b981' : m>=45 ? '#0891b2' : '#f59e0b';
    document.getElementById('more-daypart').innerHTML = dayparts.map(x=>{
      const w = Math.round(100*(x.revenue_pct||0)/maxPct);
      const isTopGp = x.est_gp_margin_pct!=null && x.est_gp_margin_pct===maxGp;
      return `<tr class="border-b border-slate-50">
        <td class="py-1.5 pr-2 font-medium">${esc(x.label)}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(x.revenue)}</td>
        <td class="py-1.5 pr-2" style="min-width:96px">
          <div class="flex items-center gap-1.5">
            <div class="flex-1" style="background:var(--bg-subtle);border-radius:3px;overflow:hidden"><div style="width:${w}%;min-width:2px;height:8px;background:var(--accent)"></div></div>
            <span class="text-[10px] text-slate-400 tabular-nums" style="width:30px">${x.revenue_pct}%</span>
          </div>
        </td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(x.covers)}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${x.rev_per_cover!=null?fmtN(x.rev_per_cover):'—'}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${x.avg_bill!=null?fmtN(x.avg_bill):'—'}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${x.drink_pct}%</td>
        <td class="py-1.5 text-right tabular-nums font-semibold" style="color:${textInk(gpCol(x.est_gp_margin_pct))}">${fmt(x.est_gross_profit)}${x.est_gp_margin_pct!=null?' · '+x.est_gp_margin_pct+'%':''}${isTopGp?'':''}</td>
      </tr>`;
    }).join('');
    const topRev = dayparts.slice().sort((a,b)=>(b.revenue||0)-(a.revenue||0))[0];
    document.getElementById('more-daypart-note').textContent = topRev ? `ช่วงพีค: ${topRev.label}` : '';
  }

  if (fc) {
    document.getElementById('more-forecast-note').textContent =
      fc.trend ? `เทรนด์ ${fmt(fc.trend.slope_per_month)}/เดือน · R²=${fc.trend.r_squared}` : (fc.note||'');
    const slope = fc.trend ? fc.trend.slope_per_month : null;
    const fb = document.getElementById('more-forecast-badge');
    if (fb) {
      if (slope == null) { fb.textContent = ''; }
      else if (slope > 0) { fb.textContent = '▲ ขาขึ้น'; fb.style.color = 'var(--good-ink)'; }
      else if (slope < 0) { fb.textContent = '▼ ขาลง'; fb.style.color = 'var(--bad-ink)'; }
      else { fb.textContent = '→ ทรงตัว'; fb.style.color = 'var(--text-muted)'; }
    }
    const hist = fc.history||[], fore = fc.forecast||[];
    const labels = [...hist.map(h=>h.period), ...fore.map(f=>f.period)];
    const histData = [...hist.map(h=>h.revenue), ...fore.map(()=>null)];
    const foreData = hist.map(()=>null);
    if (hist.length) foreData[hist.length-1] = hist[hist.length-1].revenue;  // connect
    fore.forEach(f=>foreData.push(f.revenue));
    const loData = [...hist.map(()=>null), ...fore.map(f=>f.lo)];
    const hiData = [...hist.map(()=>null), ...fore.map(f=>f.hi)];
    mkChart('more-forecast', { type:'line',
      data:{ labels, datasets:[
        { label:'จริง', data:histData, borderColor:'#4a70b0', spanGaps:false },
        { label:'พยากรณ์', data:foreData, borderColor:'#0891b2', borderDash:[6,4], spanGaps:true },
        { label:'hi', data:hiData, borderColor:'transparent', backgroundColor:'rgba(8,145,178,.12)', fill:'+1', pointRadius:0 },
        { label:'lo', data:loData, borderColor:'transparent', backgroundColor:'rgba(8,145,178,.12)', fill:false, pointRadius:0 },
      ]},
      options:{ responsive:true, maintainAspectRatio:false,
        plugins:{ legend:{ display:true, labels:{ filter:li=>li.text==='จริง'||li.text==='พยากรณ์' } }, datalabels:{display:false} },
        scales:{ y:{ ticks:{ callback:v=>fmt(v) } } } } });
  }

  const tbr = (turn && turn.branches) || [];
  if (turn) {
    const turns = tbr.filter(b=>b.avg_turn_min!=null).map(b=>b.avg_turn_min);
    const maxT = Math.max(1, ...turns), minT = Math.min(...turns);
    const med = turns.length ? turns.slice().sort((a,b)=>a-b)[Math.floor(turns.length/2)] : 0;
    const medal = i => i===0?'1.':i===1?'2.':i===2?'3.':(i+1);
    document.getElementById('more-turnover').innerHTML = tbr.map((b,i)=>{
      const t = b.avg_turn_min;
      const fast = t!=null && t<=med;
      const col = t==null ? '#94a3b8' : fast ? '#10b981' : '#f59e0b';
      const bg  = t==null ? '#f8fafc' : fast ? '#ecfdf5' : '#fffbeb';
      return `<div class="rounded-lg p-2.5 text-center on-light" style="background:${bg};border:1px solid ${col}33">
        <div class="text-[11px] font-medium text-slate-500 truncate" title="${esc(b.branch)}">${medal(i)} ${esc(b.branch)}</div>
        <div class="font-bold tabular-nums leading-tight" style="color:${textInk(col)};font-size:1.35rem">${t!=null?t:'—'}<span class="text-[10px] font-normal text-slate-400"> น./รอบ</span></div>
        <div class="text-[10px] text-slate-400 tabular-nums mt-0.5">${fmtN(b.turns)} รอบ · ${b.avg_covers_per_turn!=null?b.avg_covers_per_turn:'—'} คน/รอบ</div>
      </div>`;
    }).join('') || '<div class="col-span-full py-3 text-center text-slate-400 text-sm">—</div>';
  }

  // ── auto-generated highlight chips (from the three datasets above) ──
  const chips = [];
  if (dayparts.length) {
    const topRev = dayparts.slice().sort((a,b)=>(b.revenue||0)-(a.revenue||0))[0];
    chips.push({ icon:'', label:'ช่วงทำเงินสูงสุด', val:esc(topRev.label),
                 sub:`${fmt(topRev.revenue)} · ${topRev.revenue_pct}% ของรายได้`, c:'#4a70b0' });
    const wGp = dayparts.filter(x=>x.est_gp_margin_pct!=null).sort((a,b)=>b.est_gp_margin_pct-a.est_gp_margin_pct)[0];
    if (wGp) chips.push({ icon:'', label:'อัตรากำไรดีสุด', val:esc(wGp.label),
                 sub:`GP ${wGp.est_gp_margin_pct}% · ดื่ม ${wGp.drink_pct}%`, c:'#10b981' });
  }
  if (tbr.length) {
    const fastest = tbr.filter(b=>b.avg_turn_min!=null).sort((a,b)=>a.avg_turn_min-b.avg_turn_min)[0];
    if (fastest) chips.push({ icon:'', label:'หมุนโต๊ะเร็วสุด', val:esc(fastest.branch),
                 sub:`${fastest.avg_turn_min} นาที/รอบ`, c:'#0891b2' });
  }
  if (fc && fc.trend) {
    const up = fc.trend.slope_per_month > 0;
    chips.push({ icon: up?'▲':'▼', label:'เทรนด์รายได้', val: up?'ขาขึ้น':'ขาลง',
                 sub:`${fmt(fc.trend.slope_per_month)}/เดือน · R²=${fc.trend.r_squared}`, c: up?'#10b981':'#ef4444' });
  }
  const hc = document.getElementById('more-highlights');
  if (hc) hc.innerHTML = chips.map(c=>`
    <div class="card p-3" style="border:1px solid ${c.c}">
      <p class="text-[11px] text-slate-400">${c.icon} ${c.label}</p>
      <p class="text-lg font-bold leading-tight" style="color:${textInk(c.c)}">${c.val}</p>
      <p class="text-[11px] text-slate-500 mt-0.5">${c.sub}</p>
    </div>`).join('');
}

// ── Boot ───────────────────────────────────────────────────────
// Register plugins + apply global chart defaults if the vendor scripts are
// already loaded. If they're still loading (async), ensureChartSetup() no-ops
// and waitForCharts() below retries — so no noisy "not loaded" errors.
ensureChartSetup();

// Idempotent: registers plugins + applies global defaults the first time it can.
// Called from boot AND from mkChart, so it works regardless of the vendor-load
// race (the inline script may parse before chart.umd.min.js finishes loading).
function ensureChartSetup() {
  if (_chartSetupDone || typeof Chart === 'undefined') return;
  if (typeof ChartDataLabels !== 'undefined') Chart.register(ChartDataLabels);
  if (typeof ChartAnnotation !== 'undefined') Chart.register(ChartAnnotation);
  Chart.register(GLANCE_PLUGIN);   // js/01-core.js: depth + latest/peak/avg labels on every chart
  setupChartDefaults();
  _chartSetupDone = true;
}

// Global Chart.js defaults — one place that makes EVERY chart more legible.
// Per-chart options still override these; this only fills the gaps (typography,
// tooltips, rounded bars, tidy points/legends).
function setupChartDefaults() {
  if (typeof Chart === 'undefined') return;
  const FONT = "'KIRA UI','Noto Sans Thai',-apple-system,'Segoe UI',sans-serif";
  Chart.defaults.font.family = FONT;
  Chart.defaults.font.size = 12.5;          // owner 2026-10-19: larger, bolder text across the app
  Chart.defaults.font.weight = 500;
  Chart.defaults.color = '#475569';
  Chart.defaults.devicePixelRatio = Math.min(window.devicePixelRatio || 1, 2); // crisp lines
  // PL3 (RV3 F32): a long axis number is short on the axis ("100,000,000" -> "100M") so the tick labels are not cut off at the chart's left
  // edge; a chart that sets its own tick callback keeps it
  try {
    const lin = Chart.defaults.scales && Chart.defaults.scales.linear;
    if (lin) {
      lin.ticks = lin.ticks || {};
      const base = Chart.Ticks && Chart.Ticks.formatters && Chart.Ticks.formatters.numeric;
      lin.ticks.callback = function (v, i, ticks) {
        if (typeof v === 'number' && Math.abs(v) >= 1e6) return `${+(v / 1e6).toFixed(1)}M`;
        return base ? base.call(this, v, i, ticks) : v;
      };
    }
  } catch (e) { /* the default tick format stays */ }

  // Rounded, cleaner bars
  Chart.defaults.elements.bar.borderRadius = 7;      // rounded tip (owner 2026-10-01: softer, livelier bars)...
  Chart.defaults.elements.bar.borderSkipped = 'start'; // ...sitting flush on the baseline
  Chart.defaults.elements.bar.maxBarThickness = 46;

  // Smoother lines, smaller resting points that grow on hover (less clutter)
  Chart.defaults.elements.line.tension = 0.25;
  Chart.defaults.elements.line.borderWidth = 2.5;   // theme B (VZ2)
  Chart.defaults.elements.point.radius = 2.2;     // visible (keeps scatter charts alive) but small
  Chart.defaults.elements.point.hoverRadius = 6;
  Chart.defaults.elements.point.hitRadius = 10;

  // Tidier legends
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.pointStyle = 'rect';   // square legend markers
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.legend.labels.boxHeight = 8;
  Chart.defaults.plugins.legend.labels.padding = 14;

  // Readable tooltips: dark card, generous padding, bold title, thousand-separated
  // numbers. A custom per-chart tooltip.callbacks.label still overrides this.
  const tt = Chart.defaults.plugins.tooltip;
  tt.backgroundColor = 'rgba(15,23,42,0.96)';
  tt.titleColor = '#f8fafc';
  tt.bodyColor = '#e2e8f0';
  tt.titleFont = { family: FONT, size: 12.5, weight: '600' };
  tt.bodyFont = { family: FONT, size: 12 };
  tt.padding = 11;
  tt.cornerRadius = 8;
  tt.boxPadding = 5;
  tt.usePointStyle = true;
  tt.pointStyle = 'rect';
  tt.borderColor = 'rgba(148,163,184,0.25)';
  tt.borderWidth = 1;
  tt.callbacks.label = function(ctx) {
    try {
      let label = ctx.dataset.label ? ctx.dataset.label + ': ' : '';
      let v = ctx.parsed;
      if (v && typeof v === 'object') v = (v.y ?? v.r ?? v.x);
      if (typeof v !== 'number' || !isFinite(v)) return label + ctx.formattedValue;
      const dp = Math.abs(v) > 0 && Math.abs(v) < 100 ? 2 : 0;
      return label + v.toLocaleString('en-US', { maximumFractionDigits: dp });
    } catch (e) { return ctx.formattedValue; }
  };

  // Datalabels (the on-chart numbers): strip IEEE-754 dust, change NOTHING else.
  // The plugin's default formatter prints the RAW value, so any unrounded
  // arithmetic leaks float error onto the chart (menu drill 2026-08: pen
  // 0.5777 × 100 → "57.769999999999996"). Rounding to 4 dp kills the dust
  // while keeping every intentionally-precise label (qty/head "1.327" stays
  // "1.327") and adding no thousand separators. null still returns null (label
  // hidden), and a per-chart `datalabels.formatter` still overrides this.
  if (Chart.defaults.plugins.datalabels) {
    Chart.defaults.plugins.datalabels.formatter = function (v) {
      return (typeof v === 'number' && isFinite(v)) ? String(parseFloat(v.toFixed(4))) : v;
    };
  }
}
init().catch(console.error);

// Vendor-load race recovery: if any chart was requested before Chart.js was ready,
// wait for it to load, then re-render the active tab once so the charts appear.
(function waitForCharts(tries) {
  if (typeof Chart !== 'undefined') {
    ensureChartSetup();
    if (_chartsPending) { _chartsPending = false; try { loadActiveTab(); } catch(e){} }
    return;
  }
  if (tries > 0) setTimeout(() => waitForCharts(tries - 1), 150);
})(40);   // up to ~6s
