// 18-launcher.js — the department launcher (PR-D1, 2026-10-21): หน้าแรก on a desktop, the sidebar by department and
// หน้ารายงานทั้งหมด. It draws DEPARTMENTS / allPagesModel (js/01-core.js, the one registry) and adds no route and no
// permission rule of its own: a tile opens with switchTab / supplyGo, and a page shows only when tabAllowed (and, for a supply
// pane, the pane's own item of SUPPLY_CHIPS) lets the signed-in role open it. The tasks are the ones of หน้างาน.
// Shares the global scope of slices 01-17 (uses esc, api, API, tabAllowed, hasModule, supplyGo, _supplyItemOf,
// _supplyItemAllowed, _shmMine, _shmOpen, _noEmoji, ROLE_LABEL_TH, whoTh, _TH_MONTHS). Every name starts with `lnch`, `dept` or `alp`.
// PR-D2 (2026-10-21): the department workspace. On a desktop the page shows #deptHead above it (renderDeptHead: breadcrumb, the department's
// band and a row of icon tabs = that department's pages) instead of the cluster bar and the supply chips; the sidebar is the icon rail.
// PR-D4 (2026-10-21): the red task counts (navBadges*, GET /api/nav/badges) and the page search (pgSearch*, "/" and Ctrl+K); both are also used by the
// phone shell (js/09c-mobile.js), which loads this slice alone, so they depend on js/01-core.js only.

let _deptFocus = null;      // the department picked in the sidebar: the launcher scrolls to its card and the entry stays lit

const _DEPT_KEY = 'oshinei_dept', _DEPT_LAST_KEY = 'oshinei_dept_last';
function deptRememberedGet() { try { return localStorage.getItem(_DEPT_KEY) || null; } catch (e) { return null; } }
function deptRemember(key) { _deptCur = key; try { localStorage.setItem(_DEPT_KEY, key); } catch (e) { /* private mode: memory only */ } }
function _deptLastMap() { try { return JSON.parse(localStorage.getItem(_DEPT_LAST_KEY) || '{}') || {}; } catch (e) { return {}; } }
let _deptCur = deptRememberedGet();   // the department the person last opened a page from (the rail lights its entry while that page is open)
function _deptRole() { return (window.AUTH && window.AUTH.enabled && window.AUTH.role) || 'management'; }   // auth off = the manager's view
function _deptSvg(icon, size, cls) {
  return `<svg ${cls ? `class="${cls}" ` : ''}width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${DEPT_ICONS[icon] || DEPT_ICONS.doc}"/></svg>`;
}
// may this person open the page? the tab's own gate first; then, for a pane of the supply module, the item the role's bar lists for it
// (its mods and its roles, _supplyItemAllowed); then the entry's own `mods` / `roles` when it names any
function deptPageAllowed(p) {
  if (!tabAllowed(p.tab)) return false;
  if (bomHidden(p.tab, p.pane)) return false;                    // PR-S1: a recipe page (bom: true) while the system is in stock mode
  if (p.mods && !p.mods.some(hasModule)) return false;
  if (p.roles && !p.roles.includes(_deptRole())) return false;
  if (p.pane) {
    // the chip item(s) that name exactly this pane (not _supplyItemOf: it falls back to the tab's first item, which another role may hold)
    const items = SUPPLY_CHIPS.flatMap(c => c.items).filter(i => i.tab === p.tab && i.pane === p.pane);
    if (items.length) return items.some(_supplyItemAllowed);
  }
  return true;
}
// the departments this person sees, home department(s) first, then the rest in registry order; each with the pages it may open
function deptVisible() {
  const role = _deptRole();
  const rows = DEPARTMENTS.map((d, order) => ({ dept: d, home: d.roles.includes(role), order,
    pages: d.pages.map((p, i) => ({ p, i })).filter(x => deptPageAllowed(x.p)) })).filter(r => r.pages.length);
  // U2: another department whose every open page is already a page of the person's own department adds no
  // tile (คลัง opened ประสานงานขาย only for คิวใบสั่งซื้อ, which is the คลัง department's own page)
  const pk = p => p.tab + '|' + (p.pane || '');
  const own = new Set(rows.filter(r => r.home).flatMap(r => r.pages.map(x => pk(x.p))));
  const kept = rows.filter(r => r.home || !own.size || r.pages.some(x => !own.has(pk(x.p))));
  return kept.sort((a, b) => (Number(b.home) - Number(a.home)) || (a.order - b.order));
}
function deptOpenPage(p) { if (p.pane) supplyGo({ tab: p.tab, pane: p.pane }); else switchTab(p.tab); }
function deptGo(key, idx) {
  const d = DEPARTMENTS.find(x => x.key === key), p = d && d.pages[idx];
  if (!p) return;
  deptRemember(key); _deptFocus = null;
  const last = _deptLastMap(); last[key] = idx;
  try { localStorage.setItem(_DEPT_LAST_KEY, JSON.stringify(last)); } catch (e) { /* memory only */ }
  deptOpenPage(p);
}

// ── the sidebar: หน้าแรก, then one entry per department the person may open a page of ─────────────────────
// the rail: a desktop starts with the sidebar folded to the icon rail (the app's own .collapsed); the toggle opens it to icon + full label
// and the choice is remembered. A phone never gets the class (its strip shows the labels).
let _deptRailDone = false;
function deptRailInit() {
  const sb = document.getElementById('sidebar');
  if (_deptRailDone || !sb || !_launcherLanding()) return;
  _deptRailDone = true;
  let pref = '';
  try { pref = localStorage.getItem('oshinei_rail') || ''; } catch (e) { /* default */ }
  if (pref !== 'open' && !sb.classList.contains('collapsed')) toggleSidebar();
}
function railToggle() {
  toggleSidebar();
  try { localStorage.setItem('oshinei_rail', document.getElementById('sidebar').classList.contains('collapsed') ? 'rail' : 'open'); } catch (e) { /* not remembered */ }
}
function renderDeptNav() {
  const host = document.getElementById('deptNav');
  if (!host) return;
  deptRailInit();
  const vis = deptVisible();
  host.innerHTML = `<a href="#" class="nav-item rail-item dept-entry" id="nav-launcher" data-dept="home" onclick="deptNavHome();return false" title="หน้าแรก · งานของฉันวันนี้และแผนกทั้งหมด">`
      + `<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg><span class="nav-label">หน้าแรก</span></a>`
    + `<a href="#" class="nav-item rail-item dept-entry" id="nav-search" onclick="pgSearchOpen();return false" title="ค้นหาหน้า · Ctrl K หรือ /">`
      + `<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${_PG_SEARCH_ICON}"/></svg><span class="nav-label">ค้นหา</span></a>`
    + `<div class="nav-section dept-entry">แผนก</div>`
    + vis.map(r => `<a href="#" class="nav-item rail-item dept-entry" id="nav-dept-${r.dept.key}" data-dept="${r.dept.key}" data-nb="d:${r.dept.key}" onclick="deptNavOpen('${r.dept.key}');return false" title="${esc(r.dept.label + ' · ' + r.dept.duty)}">`
      + `${_deptSvg(r.dept.icon, 17, 'nav-icon')}<span class="nav-label">${esc(r.dept.label)}</span></a>`).join('');
  updateDeptNav(typeof activeTab === 'string' ? activeTab : '');
  navBadgesApply(); navBadgesStart(); pgSearchInit();
}
function deptNavHome() { _deptFocus = null; switchTab(franchiseLogin() ? 'franchise' : 'launcher'); }   // PR-F1: the owner's หน้าแรก is ร้านของฉัน
// a department entry of the rail: that department's workspace - the page last opened there, else its first page the person may open
function deptNavOpen(key) {
  const row = deptVisible().find(r => r.dept.key === key);
  if (!row) return;
  const lastIdx = _deptLastMap()[key], hit = row.pages.find(x => x.i === lastIdx) || row.pages[0];
  deptGo(key, hit.i);
}
// which department holds the open page: the one the person came from, else the first (home first) that lists it. A pane that no
// department lists, of a tab that one does, still belongs to that department (no tab of its row is lit).
// -> { row: {dept, pages}, cur: the lit entry {p, i} | null } | null
function deptContext(tab) {
  const acc = typeof _SUPPLY_PANE === 'object' && _SUPPLY_PANE[tab];
  const pane = acc ? acc.get() : null, rows = deptVisible();
  const exact = r => r.pages.find(x => x.p.tab === tab && (!x.p.pane || x.p.pane === pane));
  const pick = list => list.find(r => r.dept.key === _deptCur) || list[0];
  let hit = rows.filter(exact);
  if (hit.length) { const r = pick(hit); return { row: r, cur: exact(r) }; }
  hit = rows.filter(r => r.pages.some(x => x.p.tab === tab));
  return hit.length ? { row: pick(hit), cur: null } : null;
}
function _deptOf(tab) { const c = deptContext(tab); return c ? c.row.dept.key : null; }
function updateDeptNav(name) {
  const nav = document.getElementById('deptNav');
  if (!nav) return;
  const key = name === 'launcher' ? _deptFocus : (name === 'allpages' ? null : _deptOf(name));
  nav.querySelectorAll('.nav-item').forEach(el => {
    const on = el.id === 'nav-launcher' ? (name === 'launcher' && !_deptFocus) : (el.dataset.dept !== undefined && el.dataset.dept === key);
    el.classList.toggle('active', on);
    if (on) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  });
}

// ── the department workspace (PR-D2): breadcrumb, the department's band and its icon tabs, above every page ─────────────────
// Drawn into #deptHead (index.html) by _updateClusterBar on every page or pane change. A page of a department shows that
// department's band and one tab per page the person may open; a page of none keeps a plain breadcrumb. Phone (<= 700 px): CSS hides it.
function _deptCrumbHtml(parts, last) {
  const home = `<a href="#" onclick="deptNavHome();return false">หน้าแรก</a>`;
  return `<nav class="dh-crumb" aria-label="ตำแหน่งของหน้านี้">${home}`
    + parts.map(x => `<span class="dh-sep" aria-hidden="true">›</span>${x}`).join('')
    + `<span class="dh-sep" aria-hidden="true">›</span><b aria-current="page">${esc(last)}</b><span id="deptLock" class="dh-lock"></span></nav>`;
}
function _deptTabsFade(el) {
  if (!el) return;
  const max = el.scrollWidth - el.clientWidth;
  el.classList.toggle('ovf-l', el.scrollLeft > 4);
  el.classList.toggle('ovf-r', max > 4 && el.scrollLeft < max - 4);
}
function renderDeptHead(name) {
  const host = document.getElementById('deptHead');
  if (!host) return;
  if (name === 'launcher') { host.hidden = true; host.innerHTML = ''; return; }
  const refocus = host.contains(document.activeElement) && document.activeElement.getAttribute('role') === 'tab';
  const ctx = deptContext(name), supply = !!(clusterOf(name) && clusterOf(name).chips);
  let title = _noEmoji(PAGE_TITLES[name] || name);
  if (supply) {   // a page of the supply module is named by its own item (the title above is "module · chip · item")
    const acc = typeof _SUPPLY_PANE === 'object' && _SUPPLY_PANE[name], hit = _supplyItemOf(name, acc ? acc.get() : null);
    if (hit) title = _noEmoji(hit.item.label);
  }
  host.hidden = false;
  if (name === 'allpages' || !ctx) {
    // a page of no department (or the list of all of them): home › หน้ารายงานทั้งหมด › the page
    host.className = 'dept-head dh-plain';
    host.removeAttribute('style');
    host.innerHTML = _deptCrumbHtml(name === 'allpages' ? [] : [`<a href="#" onclick="switchTab('allpages');return false">หน้ารายงานทั้งหมด</a>`],
      name === 'allpages' ? 'หน้ารายงานทั้งหมด' : title);
    if (supply) _renderLockChip();
    return;
  }
  const d = ctx.row.dept, cur = ctx.cur;
  host.className = 'dept-head';
  host.setAttribute('style', `--dk:var(--dept-${d.hue}-ink);--dt:var(--dept-${d.hue}-tint)`);
  const tabs = ctx.row.pages.map(x => {
    const on = !!cur && cur.i === x.i;
    return `<button type="button" role="tab" class="dh-tab" aria-selected="${on}" tabindex="${on || (!cur && x === ctx.row.pages[0]) ? 0 : -1}"`
      + ` data-i="${x.i}" data-nb="${_navPageSpec(x.p)}" onclick="deptGo('${d.key}',${x.i})" onkeydown="deptTabKey(event)">${_deptSvg(x.p.icon, 20)}<span>${esc(x.p.label)}</span></button>`;
  }).join('');
  host.innerHTML = _deptCrumbHtml([`<span class="dh-dept">${esc(d.label)}</span>`], cur ? cur.p.label : title)
    + `<div class="dh-band${ctx.row.pages.length > 7 ? ' many' : ''}"><div class="dh-id"><span class="dh-badge">${_deptSvg(d.icon, 26)}</span>`
    + `<div class="dh-idtext"><h2 class="dh-name">${esc(d.label)}</h2><span class="dh-duty">${esc(d.duty)}</span></div></div>`
    + `<div class="dh-tabs" role="tablist" aria-label="${esc('หน้าของแผนก' + d.label)}" onscroll="_deptTabsFade(this)">${tabs}</div></div>`;
  const bar = host.querySelector('.dh-tabs'), act = bar.querySelector('[aria-selected="true"]');
  if (act) bar.scrollLeft = Math.max(0, act.offsetLeft - (bar.clientWidth - act.offsetWidth) / 2);
  _deptTabsFade(bar);
  if (refocus) (act || bar.querySelector('[role="tab"]')).focus({ preventScroll: true });
  if (supply) _renderLockChip();
  navBadgesApply(); navBadgesRefresh(false);
}
// arrow keys move between the tabs of the row (Home / End jump); Enter and Space open the focused one, as on any button
function deptTabKey(ev) {
  const tabs = [...ev.currentTarget.parentElement.querySelectorAll('[role="tab"]')], i = tabs.indexOf(ev.currentTarget);
  const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[ev.key];
  if (to === undefined) return;
  ev.preventDefault();
  const t = tabs[(to + tabs.length) % tabs.length];
  tabs.forEach(x => x.tabIndex = -1); t.tabIndex = 0; t.focus();
}

// ── หน้าแรก ──────────────────────────────────────────────────────────────────────────────────────────────
const _LNCH_WEEKDAYS = ['อาทิตย์', 'จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์'];
function lnchDateLabel() {   // "วันพุธ 21 ตุลาคม 2026" - today by the Bangkok clock, in the year the rest of the app writes
  const iso = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Bangkok' }), p = _isoParts(iso);
  return p ? `วัน${_LNCH_WEEKDAYS[new Date(Date.UTC(p[0], p[1] - 1, p[2])).getUTCDay()]} ${p[2]} ${_TH_MONTHS[p[1] - 1]} ${p[0]}` : '';
}
function lnchWho() {   // the name a person recognises: the display name, else the role's label; never the raw login
  const a = window.AUTH || {};
  if (!a.enabled) return 'ผู้บริหาร';
  const n = a.display_name || (a.names && a.names[a.username]);
  return n || String(ROLE_LABEL_TH[a.role] || a.role || '').split(' (')[0] || 'ผู้ใช้งาน';
}
function lnchFocusDept(key) {
  const el = document.getElementById('lnch-dept-' + key);
  if (!el) return;
  document.querySelectorAll('#lnchDepts .lnch-dept.is-focus').forEach(x => x.classList.remove('is-focus'));
  el.classList.add('is-focus');
  try { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { el.scrollIntoView(); }
  const h = el.querySelector('.lnch-dept-name');
  if (h) { h.setAttribute('tabindex', '-1'); try { h.focus({ preventScroll: true }); } catch (e) { /* no focus on a hidden page */ } }
  setTimeout(() => el.classList.remove('is-focus'), 2500);
}
function _lnchDeptsHtml(vis) {
  return vis.map(r => {
    const d = r.dept;
    return `<section class="lnch-dept" id="lnch-dept-${d.key}" data-dept="${d.key}" aria-labelledby="lnch-h-${d.key}" style="--dk:var(--dept-${d.hue}-ink);--dt:var(--dept-${d.hue}-tint)">`
      + `<div class="lnch-dept-head"><span class="lnch-badge">${_deptSvg(d.icon, 26)}</span>`
      + `<div style="min-width:0"><h3 class="lnch-dept-name" id="lnch-h-${d.key}">${esc(d.label)}</h3><div class="lnch-dept-duty">${esc(d.duty)}</div></div></div>`
      + `<div class="lnch-tiles">${r.pages.map(x => `<a href="#" class="lnch-tile" data-nb="${_navPageSpec(x.p)}" onclick="deptGo('${d.key}',${x.i});return false">${_deptSvg(x.p.icon, 28)}`
        + `<span class="lnch-tile-label">${esc(x.p.label)}</span></a>`).join('')}</div></section>`;
  }).join('');
}
function _lnchFootHtml() {
  const a = (js, t) => `<a href="#" onclick="${js};return false">${t}</a>`;
  return a("switchTab('manual')", 'คู่มือการใช้งาน') + (tabAllowed('settings') ? a("switchTab('settings')", 'ตั้งค่า') : '') + a("switchTab('allpages')", 'หน้ารายงานทั้งหมด');
}
async function loadLauncher() {
  const vis = deptVisible();
  document.getElementById('lnchDate').textContent = lnchDateLabel();
  document.getElementById('lnchGreet').textContent = `สวัสดี ${lnchWho()}`;
  document.getElementById('lnchDepts').innerHTML = _lnchDeptsHtml(vis);
  document.getElementById('lnchFoot').innerHTML = _lnchFootHtml();
  updateDeptNav('launcher');
  if (_deptFocus) lnchFocusDept(_deptFocus);
  navBadgesApply(); navBadgesStart(); navBadgesRefresh(false);
  await lnchLoadTasks();
}

// ── งานของฉันวันนี้: the items of หน้างาน that belong to this role (same filter as หน้างาน and the Daily Brief), by state (PR-TF) ──
// Three tabs - ยังไม่เสร็จ · รอตรวจ · เสร็จแล้ว - each with its count; the choice is the one of หน้างาน (shmFilterGet / shmFilterSet,
// localStorage `oshinei_task_filter`), "ทั้งหมด" there shows as ยังไม่เสร็จ here. At most 5 rows; "ดูทั้งหมด" opens หน้างาน on the same tab.
const _LNCH_TABS = [['todo', 'ยังไม่เสร็จ'], ['review', 'รอตรวจ'], ['done', 'เสร็จแล้ว']];
let _lnchGroups = { todo: [], review: [], done: [] };    // the rows by state: the same duty of several branches is ONE row naming how many
let _lnchShown = [];
function _lnchFilter() { const f = shmFilterGet(); return f === 'all' ? 'todo' : f; }
function _lnchGroup(list) {
  const groups = [];
  list.slice().sort((a, b) => ((b.urgent ? 1 : 0) - (a.urgent ? 1 : 0)) || (a.seq - b.seq)).forEach(i => {
    const k = String(i.key || '').split(':')[0] + '|' + (i.title || '');
    const g = groups.find(x => x.k === k);
    if (g) { if (i.branch && !g.branches.includes(i.branch)) g.branches.push(i.branch); g.urgent = g.urgent || !!i.urgent; }
    else groups.push({ k, first: i, branches: i.branch ? [i.branch] : [], urgent: !!i.urgent });
  });
  return groups;
}
let _lnchOpenN = null;          // PR-U1: the number of open tasks (the server's mine_open) - the same figure as the badges and หน้างาน
function _lnchTabN(k) { return k === 'todo' && _lnchOpenN != null ? _lnchOpenN : _lnchGroups[k].length; }
function _lnchTabsHtml(f) {
  return _LNCH_TABS.map(([k, t]) => `<button type="button" class="lnch-tab" data-f="${k}" aria-pressed="${f === k}" onclick="lnchTaskFilter('${k}')">${t} <span class="lnch-tab-n">(${_lnchTabN(k)})</span></button>`).join('');
}
function lnchRenderTasks() {
  const rows = document.getElementById('lnchTaskRows'), tabs = document.getElementById('lnchTaskTabs');
  if (!rows) return;
  const f = _lnchFilter(), groups = _lnchGroups[f];
  tabs.style.display = ''; tabs.innerHTML = _lnchTabsHtml(f);
  _lnchShown = groups.slice(0, 5);
  if (!groups.length) { rows.innerHTML = `<div class="lnch-empty">${f === 'todo' ? 'ไม่มีงานค้างวันนี้' : 'ไม่มีงานในสถานะนี้'}</div>`; return; }
  rows.innerHTML = _lnchShown.map((g, n) => {
    const it = g.first, go = it.go && it.go.tab && tabAllowed(it.go.tab) ? it.go : null;
    const who = g.branches.length > 1 ? ` · ${g.branches.length} สาขา` : (g.branches.length ? ` (${g.branches[0]})` : '');
    return `<div class="lnch-row"><span class="lnch-dot${g.urgent && f !== 'done' ? ' is-urgent' : ''}${f === 'review' ? ' is-review' : ''}${f === 'done' ? ' is-done' : ''}" aria-hidden="true"></span>`
      + `<span class="lnch-row-title">${esc(it.title)}${esc(who)}</span><span class="lnch-row-dept">${esc(it.owner_th || '')}</span>`
      + (go ? `<a href="#" class="lnch-go" onclick="lnchTaskGo(${n});return false">${f === 'done' ? 'ดู' : 'ไปที่งาน'}</a>` : '') + `</div>`;
  }).join('') + (groups.length > _lnchShown.length ? `<div class="lnch-empty">และอีก ${groups.length - _lnchShown.length} เรื่อง</div>` : '');
}
function lnchTaskFilter(k) { shmFilterSet(k); lnchRenderTasks(); }
async function lnchLoadTasks() {
  const rows = document.getElementById('lnchTaskRows'), tabs = document.getElementById('lnchTaskTabs'), all = document.getElementById('lnchTaskAll');
  if (!rows) return;
  tabs.style.display = 'none'; all.style.display = 'none';
  // a role with no supply module has no task list: the Daily Brief (or, for the branch owner, the shop's own summary) stands in
  if (!tabAllowed('supplyhome')) {
    const alt = [['home', 'สรุปประจำวันและสิ่งที่ต้องดูวันนี้'], ['franchise', 'สรุปผลการดำเนินงานของร้านตนเอง']].find(x => tabAllowed(x[0]));
    rows.innerHTML = alt
      ? `<div class="lnch-row"><span class="lnch-dot" aria-hidden="true"></span><span class="lnch-row-title">${esc(alt[1])}</span>`
        + `<a href="#" class="lnch-go" onclick="switchTab('${alt[0]}');return false">เปิดดู</a></div>`
      : '<div class="lnch-empty">ไม่มีรายการงานสำหรับบัญชีนี้</div>';
    return;
  }
  rows.innerHTML = '<div class="lnch-empty">กำลังโหลด…</div>';
  let todo = null;
  try { todo = await api(`${API}/api/bom-center/todo`); } catch (e) { todo = null; }
  if (activeTab !== 'launcher') return;                       // the person already moved on
  if (!todo || !Array.isArray(todo.items)) { rows.innerHTML = '<div class="lnch-empty">โหลดรายการงานไม่ได้ ลองเปิดหน้าใหม่อีกครั้ง</div>'; return; }
  // PR-U1: the items and, for a role that owns steps of the month close, those steps - the open ones are exactly the server's mine_open
  const role = (window.AUTH && window.AUTH.enabled && window.AUTH.role) || '';
  const steps = ['analyst', 'salessupport', 'accounting'].includes(role) ? ((todo.close && todo.close.steps) || []) : [];
  const keys = shmOpenKeys(todo);
  const mine = todo.items.concat(steps).filter(i => _shmMine(i) && !i.upcoming);
  _lnchGroups = { todo: [], review: [], done: [] };
  _LNCH_TABS.forEach(([k]) => { _lnchGroups[k] = _lnchGroup(mine.filter(i => shmState(i) === k && (k !== 'todo' || shmIsOpenTask(i, keys)))); });
  _lnchOpenN = keys ? keys.size : null;
  all.style.display = ''; all.textContent = 'ดูทั้งหมด';
  lnchRenderTasks();
}
function lnchTaskGo(n) { const g = _lnchShown[n]; if (g && g.first.go) { _deptFocus = null; _shmOpen(g.first.go); } }
function lnchTasksAll() { _deptFocus = null; shmFilterSet(_lnchFilter()); supplyGo({ tab: 'supplyhome' }); }

// ── task count badges (PR-D4, 2026-10-21) ───────────────────────────────────────────────────────────────────────────────
// GET /api/nav/badges answers { pages: { "<tab>" | "<tab>:<pane>": n }, at }: the tasks that wait for THIS person's own move (state "todo" only -
// never รอตรวจ, never เสร็จแล้ว), counted on the server from the items of หน้างาน. A red count (1 to 99, then 99+) is drawn on every element that
// carries `data-nb`:
//   data-nb="p:<tab>[:<pane>]"  a page (launcher tile, department tab, phone tile, search row)
//   data-nb="d:<dept>"          a department: the sum of its pages the person may open (rail icon; the phone's .ph-cnt carries data-dept)
//   data-nb="t"                 the person's whole list (the phone's bottom-nav "งานของฉัน")
// navBadgesApply() redraws the counts in place - no re-render, so focus and scroll stay. The data is fetched on load, whenever the tab is shown
// again and every 5 minutes; a failed fetch keeps the last count for 15 minutes and then shows none - never an error on the screen.
const _NB_EVERY_MS = 5 * 60 * 1000, _NB_MIN_GAP_MS = 45 * 1000, _NB_KEEP_MS = 15 * 60 * 1000;
const _NB = { pages: /** @type {Record<string, number>} */ ({}), at: 0, tried: 0, busy: false, started: false };
function _navPageSpec(p) { return 'p:' + p.tab + (p.pane ? ':' + p.pane : ''); }
function navPageCount(p) { return _NB.pages[p.tab + (p.pane ? ':' + p.pane : '')] || 0; }
function navDeptCounts() {   // { department key: n } over the pages the person may open
  /** @type {Record<string, number>} */
  const out = {};
  deptVisible().forEach(r => { out[r.dept.key] = r.pages.reduce((n, x) => n + navPageCount(x.p), 0); });
  return out;
}
function navBadgeText(n) { return n > 99 ? '99+' : String(n); }
function navBadgesApply() {
  const els = document.querySelectorAll('[data-nb], .ph-cnt[data-dept]');
  if (!els.length) return;
  let depts = null;
  els.forEach(el => {
    const own = el.classList.contains('ph-cnt');          // the phone's placeholder IS the badge; every other host gets a child
    let n = 0;
    if (own) { depts = depts || navDeptCounts(); n = depts[el.dataset.dept] || 0; }
    else {
      const spec = el.dataset.nb || '';
      if (spec === 't') n = Object.values(_NB.pages).reduce((a, v) => a + (v > 0 ? v : 0), 0);
      else if (spec.startsWith('d:')) { depts = depts || navDeptCounts(); n = depts[spec.slice(2)] || 0; }
      else if (spec.startsWith('p:')) n = _NB.pages[spec.slice(2)] || 0;
    }
    let b = own ? el : el.querySelector(':scope > .nb');
    if (n < 1) { if (b) { if (own) b.hidden = true; else b.remove(); } return; }
    if (!b) { b = document.createElement('span'); b.className = 'nb'; el.appendChild(b); }
    b.hidden = false;
    b.setAttribute('role', 'img');
    b.setAttribute('aria-label', `รอ ${n} งาน`);
    b.textContent = navBadgeText(n);
  });
}
async function navBadgesRefresh(force) {
  if (_NB.busy || (!force && Date.now() - _NB.tried < _NB_MIN_GAP_MS)) return;
  _NB.busy = true; _NB.tried = Date.now();
  try {
    const j = await getJSON('/api/nav/badges');                    // throws on a non-2xx answer
    if (!j || !j.pages || typeof j.pages !== 'object') throw new Error('shape');
    _NB.pages = j.pages; _NB.at = Date.now();
  } catch (e) {
    if (Date.now() - _NB.at > _NB_KEEP_MS) _NB.pages = {};     // no badges rather than an old number; nothing is shown about the failure
  } finally { _NB.busy = false; }
  navBadgesApply();
}
function navBadgesStart() {
  if (_NB.started) return;
  _NB.started = true;
  document.addEventListener('visibilitychange', () => { if (!document.hidden) navBadgesRefresh(false); });
  setInterval(() => { if (!document.hidden) navBadgesRefresh(true); }, _NB_EVERY_MS);
  navBadgesRefresh(true);
}

// ── page search (PR-D4) ─────────────────────────────────────────────────────────────────────────────────────────────────
// ONE engine for the desktop panel (the launcher's search box, the rail's search icon, "/" and Ctrl+K) and the phone's ค้นหา page. The index is
// every page the person may open: the pages of DEPARTMENTS first (with their department), then the rest of หน้ารายงานทั้งหมด. A page is found by its
// label, its words (PAGE_KEYWORDS in js/01-core.js), its department or its old cluster. No server call.
const _PG_SEARCH_ICON = 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z M20 20l-3.5-3.5';
const _PG_MAX = 12, _PG_IDLE = 8;
function pgNorm(s) { return String(s || '').normalize('NFC').toLowerCase().replace(/[\s​ ]+/g, ''); }   // spaces do not matter, Latin is lower-case
function _pgEntry(tab, pane, label, icon, dept, di, cluster) {
  const kw = PAGE_KEYWORDS[tab + (pane ? '|' + pane : '')] || '';
  return { tab, pane, label, icon, dept, di, cluster, n: pgNorm(label), nd: pgNorm(dept ? dept.label : ''), nc: pgNorm(cluster),
    nk: kw.split(/\s+/).filter(Boolean).map(pgNorm) };
}
function pgIndex(extra) {
  const seen = new Set(), out = [], role = _deptRole();
  /** @type {Record<string, string>} */
  const cluster = {};
  allPagesModel(() => true).forEach(g => g.entries.forEach(e => { const k = e.tab + '|' + (e.pane || ''); if (!cluster[k]) cluster[k] = _noEmoji(g.label); }));
  deptVisible().forEach(r => r.pages.forEach(x => {
    const k = x.p.tab + '|' + (x.p.pane || '');
    if (seen.has(k)) return;                                  // a page of several departments is listed once, under the first (the home one)
    seen.add(k);
    out.push(_pgEntry(x.p.tab, x.p.pane, _noEmoji(x.p.label), x.p.icon, r.dept, x.i, cluster[k] || ''));
  }));
  allPagesModel(e => (!e.roles || e.roles.includes(role)) && (e.item ? _supplyItemAllowed(e.item) : tabAllowed(e.tab))).forEach(g => g.entries.forEach(e => {
    const k = e.tab + '|' + (e.pane || '');
    if (seen.has(k)) return;
    seen.add(k);
    out.push(_pgEntry(e.tab, e.pane, _noEmoji(e.label), 'doc', null, -1, _noEmoji(g.label)));
  }));
  (extra || []).slice().reverse().forEach(x => out.unshift(Object.assign(_pgEntry('', '', x.label, x.icon || 'doc', x.dept || null, -1, ''), x, { extra: true })));
  return out;
}
// the best match of one normalised word against a page: label starts with it 100 > label has it 80 > a page word is it 75 / starts with it 70 / has it 55
// > the department has it 40 > the old cluster has it 30; 0 = no match
function _pgScore(e, q) {
  if (!q) return 0;
  if (e.n.startsWith(q)) return 100;
  if (e.n.includes(q)) return 80;
  if (e.nk.some(w => w === q)) return 75;
  if (e.nk.some(w => w.startsWith(q))) return 70;
  if (e.nk.some(w => w.includes(q))) return 55;
  if (e.nd.includes(q)) return 40;
  if (e.nc.includes(q)) return 30;
  return 0;
}
// -> the pages for the typed text, best first (ties keep the registry order); no text = the pages with a count, then the home department's pages
// (opt.all: every page, as the phone's ค้นหา page lists them before anything is typed)
function pgSearchResults(text, extra, opt) {
  const idx = pgIndex(extra), q = pgNorm(text);
  if (!q && opt && opt.all) return idx;
  if (!q) {
    const role = _deptRole();
    const counted = idx.filter(e => e.tab && navPageCount(e) > 0).sort((a, b) => navPageCount(b) - navPageCount(a));
    return counted.concat(idx.filter(e => e.dept && e.dept.roles.includes(role) && !counted.includes(e))).concat(idx.filter(e => e.extra)).slice(0, _PG_IDLE);
  }
  const words = String(text).trim().split(/\s+/).map(pgNorm).filter(Boolean);
  const scored = idx.map((e, order) => {
    let s = _pgScore(e, q);
    if (words.length > 1) { const each = words.map(w => _pgScore(e, w)); if (each.every(v => v > 0)) s = Math.max(s, Math.min(...each) * 0.9); }
    return { e, s, order };
  }).filter(x => x.s > 0);
  // a department or cluster name only finds pages when nothing is named better (a whole cluster would otherwise flood the list)
  const floor = scored.some(x => x.s >= 50) ? 50 : 0;
  return scored.filter(x => x.s >= floor).sort((a, b) => (b.s - a.s) || (a.order - b.order)).slice(0, _PG_MAX).map(x => x.e);
}
// icon + name + department chip; the count of the page is added by navBadgesApply (data-nb on the row)
function pgRowStyle(e) {
  return e.dept ? `--dk:var(--dept-${e.dept.hue}-ink);--dt:var(--dept-${e.dept.hue}-tint)` : '--dk:var(--text-secondary);--dt:var(--bg-subtle)';
}
function pgRowInner(e) {
  const chip = e.dept ? e.dept.label : (String(e.cluster || '').split(' · ')[0] || 'หน้ารายงานทั้งหมด');
  return `<span class="pgs-ic">${_deptSvg(e.icon, 22)}</span><span class="pgs-name">${esc(e.label)}</span><span class="pgs-chip">${esc(chip)}</span>`;
}
function pgRowNb(e) { return e.tab ? ` data-nb="${_navPageSpec(e)}"` : ''; }
// open a result: a page of a department opens in that department (its workspace tabs), the rest as a link of หน้ารายงานทั้งหมด
function pgOpen(e) {
  _deptFocus = null;
  if (e.dept && e.di >= 0) deptGo(e.dept.key, e.di); else alpGo(e.tab, e.pane);
}

// the desktop panel
/** @type {{list: any[], sel: number, opener: any}} */
const _pg = { list: [], sel: 0, opener: null };
let _pgKeysDone = false;
function _pgPanel() {
  let el = document.getElementById('pgSearch');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'pgSearch'; el.className = 'pgs-back'; el.hidden = true;
  el.innerHTML = `<div class="pgs-box" role="dialog" aria-modal="true" aria-label="ค้นหาหน้า">`
    + `<div class="pgs-head"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${_PG_SEARCH_ICON}"/></svg>`
    + `<input id="pgsInput" class="pgs-input" type="text" role="combobox" aria-expanded="true" aria-controls="pgsList" aria-autocomplete="list" aria-label="ค้นหาหน้า" placeholder="ค้นหาหน้า เช่น ใบสั่งซื้อ ต้นทุน เคลม" autocomplete="off" spellcheck="false" enterkeyhint="go">`
    + `<button type="button" class="pgs-x" id="pgsClose" aria-label="ปิด"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12 M18 6L6 18"/></svg></button></div>`
    + `<div id="pgsList" class="pgs-list" role="listbox" aria-label="ผลการค้นหา"></div></div>`;
  document.body.appendChild(el);
  el.addEventListener('mousedown', ev => { if (ev.target === el) { ev.preventDefault(); pgSearchClose(); } });
  const input = /** @type {HTMLInputElement} */ (el.querySelector('#pgsInput')), list = /** @type {HTMLElement} */ (el.querySelector('#pgsList'));
  input.addEventListener('input', () => pgSearchRender());
  el.addEventListener('keydown', pgSearchKey);
  /** @type {HTMLElement} */ (el.querySelector('#pgsClose')).addEventListener('click', () => pgSearchClose());
  list.addEventListener('click', ev => { const r = /** @type {HTMLElement} */ (ev.target).closest('[data-i]'); if (r) pgSearchGo(Number(r.getAttribute('data-i'))); });
  list.addEventListener('mousemove', ev => { const r = /** @type {HTMLElement} */ (ev.target).closest('[data-i]'); const i = r ? Number(r.getAttribute('data-i')) : -1; if (i >= 0 && i !== _pg.sel) pgSearchSelect(i, false); });
  return el;
}
function pgSearchRender() {
  const input = /** @type {HTMLInputElement} */ (document.getElementById('pgsInput')), list = document.getElementById('pgsList');
  if (!input || !list) return;
  _pg.list = pgSearchResults(input.value);
  _pg.sel = 0;
  list.innerHTML = _pg.list.length
    ? _pg.list.map((e, i) => `<div class="pgs-row" role="option" id="pgs-o${i}" data-i="${i}" aria-selected="${i === 0}"${pgRowNb(e)} style="${pgRowStyle(e)}">${pgRowInner(e)}</div>`).join('')
    : '<div class="pgs-none">ไม่พบหน้า</div>';
  input.setAttribute('aria-activedescendant', _pg.list.length ? 'pgs-o0' : '');
  navBadgesApply();
}
function pgSearchSelect(i, scroll) {
  if (!_pg.list.length) return;
  _pg.sel = (i + _pg.list.length) % _pg.list.length;
  document.querySelectorAll('#pgsList .pgs-row').forEach((r, n) => r.setAttribute('aria-selected', String(n === _pg.sel)));
  const cur = document.getElementById('pgs-o' + _pg.sel), input = document.getElementById('pgsInput');
  if (input) input.setAttribute('aria-activedescendant', 'pgs-o' + _pg.sel);
  if (cur && scroll !== false) cur.scrollIntoView({ block: 'nearest' });
}
function pgSearchKey(ev) {
  const k = ev.key;
  if (k === 'Escape') { ev.preventDefault(); ev.stopPropagation(); pgSearchClose(); return; }
  if (ev.isComposing) return;                                    // a Thai or other IME is still composing: Enter and arrows belong to it
  if (k === 'ArrowDown') { ev.preventDefault(); pgSearchSelect(_pg.sel + 1); }
  else if (k === 'ArrowUp') { ev.preventDefault(); pgSearchSelect(_pg.sel - 1); }
  else if (k === 'Enter') { ev.preventDefault(); if (_pg.list.length) pgSearchGo(_pg.sel); }
  else if (k === 'Tab') {                                        // the dialog keeps the focus: the field and the close button
    const input = document.getElementById('pgsInput'), x = document.getElementById('pgsClose');
    if (ev.shiftKey && document.activeElement === input) { ev.preventDefault(); if (x) x.focus(); }
    else if (!ev.shiftKey && document.activeElement === x) { ev.preventDefault(); if (input) input.focus(); }
  }
}
function pgSearchOpen(seed) {
  if (!_launcherLanding() || phOn()) return;                    // a phone has its own ค้นหา page
  const el = _pgPanel();
  if (!el.hidden) return;
  const act = document.activeElement;
  _pg.opener = act && act !== document.body ? act : null;
  el.hidden = false;
  document.body.classList.add('pgs-open');
  const input = /** @type {HTMLInputElement} */ (document.getElementById('pgsInput'));
  input.value = seed || '';
  pgSearchRender();
  input.focus();
}
function pgSearchClose(noFocus) {
  const el = document.getElementById('pgSearch');
  if (!el || el.hidden) return;
  el.hidden = true;
  document.body.classList.remove('pgs-open');
  const back = _pg.opener; _pg.opener = null;
  if (!noFocus && back && document.contains(back)) { try { back.focus({ preventScroll: true }); } catch (e) { /* the opener is gone */ } }
}
function pgSearchGo(i) {
  const e = _pg.list[i];
  if (!e) return;
  pgSearchClose(true);
  pgOpen(e);
}
// "/" and Ctrl+K (Cmd+K) open the panel from anywhere but a field; the physical keys also work on a Thai layout (ฝ and า)
function _pgBlocked() {
  const shown = (/** @type {string} */ id) => { const x = document.getElementById(id); return !!x && getComputedStyle(x).display !== 'none'; };
  if (shown('loginOverlay') || shown('pwOverlay')) return true;
  return [...document.querySelectorAll('[aria-modal="true"]')].some(x => !x.closest('#pgSearch') && x.getClientRects().length > 0);
}
function pgSearchInit() {
  if (_pgKeysDone) return;
  _pgKeysDone = true;
  document.addEventListener('keydown', ev => {
    if (ev.defaultPrevented || ev.isComposing || !_launcherLanding() || phOn()) return;
    const el = document.getElementById('pgSearch'), open = !!el && !el.hidden;
    const key = ev.key, mod = (ev.ctrlKey || ev.metaKey) && !ev.altKey && !ev.shiftKey;
    if (mod && (key === 'k' || key === 'K' || ev.code === 'KeyK')) { ev.preventDefault(); if (open) pgSearchClose(); else if (!_pgBlocked()) pgSearchOpen(); return; }
    if (open || ev.ctrlKey || ev.metaKey || ev.altKey || !(key === '/' || ev.code === 'Slash')) return;
    const t = /** @type {HTMLElement} */ (ev.target);
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (_pgBlocked()) return;
    ev.preventDefault();
    pgSearchOpen();
  });
}

// ── หน้ารายงานทั้งหมด ────────────────────────────────────────────────────────────────────────────────────────
function alpGo(tab, pane) { _deptFocus = null; if (pane) supplyGo({ tab, pane }); else switchTab(tab); }
function loadAllPages() {
  const body = document.getElementById('alpBody');
  if (!body) return;
  const groups = allPagesModel(e => (!e.roles || e.roles.includes(_deptRole())) && (e.item ? _supplyItemAllowed(e.item) : tabAllowed(e.tab)));
  updateDeptNav('allpages');
  body.innerHTML = groups.length ? groups.map(g => `<section class="alp-card"><h2>${esc(_noEmoji(g.label))}</h2><ul>`
    + g.entries.map(e => `<li><a href="#" onclick="alpGo('${e.tab}'${e.pane ? `,'${e.pane}'` : ''});return false">${esc(_noEmoji(e.label))}</a></li>`).join('')
    + '</ul></section>').join('') : '<div class="alp-lead">บัญชีนี้ยังไม่มีหน้าที่เปิดได้</div>';
}

// ── แผนที่เมนูตามแผนก (PR-D5, 2026-10-21): the manual's map of the ten departments ───────────────────────────────────────
// Drawn from DEPARTMENTS on every visit to คู่มือการใช้งาน (TABS.manual.load), so the map never drifts from the menu: the icon, the name, the
// duty line, the role(s) whose home it is, and every page as a link. A page this account may not open stays in the list, dimmed.
function renderManualDeptMap() {
  const host = document.getElementById('manualDeptMapBody');
  if (!host) return;
  const open = new Set(deptVisible().flatMap(r => r.pages.map(x => r.dept.key + '|' + x.i)));
  const who = r => String(ROLE_LABEL_TH[r] || r).split(' (')[0];
  host.innerHTML = DEPARTMENTS.map(d => {
    const pages = d.pages.map((p, i) => open.has(d.key + '|' + i)
      ? `<a href="#" class="mdm-pg" onclick="deptGo('${d.key}',${i});return false">${_deptSvg(p.icon, 16)}<span>${esc(p.label)}</span></a>`
      : `<span class="mdm-pg is-off" title="บัญชีนี้ไม่มีสิทธิ์เปิดหน้านี้">${_deptSvg(p.icon, 16)}<span>${esc(p.label)}</span></span>`).join('');
    return `<section class="mdm-card" data-dept="${d.key}" style="--dk:var(--dept-${d.hue}-ink);--dt:var(--dept-${d.hue}-tint)">`
      + `<header class="mdm-head"><span class="mdm-badge">${_deptSvg(d.icon, 24)}</span><div><h4 class="mdm-name">${esc(d.label)}</h4><p class="mdm-duty">${esc(d.duty)}</p></div></header>`
      + `<p class="mdm-who">แผนกหลักของ: ${esc(d.roles.map(who).join(' · '))}</p><div class="mdm-pages">${pages}</div></section>`;
  }).join('');
}
