// 18-launcher.js — the department launcher (PR-D1, 2026-10-21): หน้าแรก on a desktop, the sidebar by department and
// หน้ารายงานทั้งหมด. It draws DEPARTMENTS / allPagesModel (js/01-core.js, the one registry) and adds no route and no
// permission rule of its own: a tile opens with switchTab / supplyGo, and a page shows only when tabAllowed (and, for a supply
// pane, the pane's own item of SUPPLY_CHIPS) lets the signed-in role open it. The tasks are the ones of หน้างาน.
// Shares the global scope of slices 01-17 (uses esc, api, API, tabAllowed, hasModule, supplyGo, _supplyItemOf,
// _supplyItemAllowed, _shmMine, _shmOpen, _noEmoji, ROLE_LABEL_TH, whoTh, _TH_MONTHS). Every name starts with `lnch`, `dept` or `alp`.
// PR-D2 (2026-10-21): the department workspace. On a desktop the page shows #deptHead above it (renderDeptHead: breadcrumb, the department's
// band and a row of icon tabs = that department's pages) instead of the cluster bar and the supply chips; the sidebar is the icon rail.

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
  return rows.sort((a, b) => (Number(b.home) - Number(a.home)) || (a.order - b.order));
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
    + `<div class="nav-section dept-entry">แผนก</div>`
    + vis.map(r => `<a href="#" class="nav-item rail-item dept-entry" id="nav-dept-${r.dept.key}" data-dept="${r.dept.key}" onclick="deptNavOpen('${r.dept.key}');return false" title="${esc(r.dept.label + ' · ' + r.dept.duty)}">`
      + `${_deptSvg(r.dept.icon, 17, 'nav-icon')}<span class="nav-label">${esc(r.dept.label)}</span></a>`).join('');
  updateDeptNav(typeof activeTab === 'string' ? activeTab : '');
}
function deptNavHome() { _deptFocus = null; switchTab('launcher'); }
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
    const on = el.id === 'nav-launcher' ? (name === 'launcher' && !_deptFocus) : el.dataset.dept === key;
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
      + ` data-i="${x.i}" onclick="deptGo('${d.key}',${x.i})" onkeydown="deptTabKey(event)">${_deptSvg(x.p.icon, 20)}<span>${esc(x.p.label)}</span></button>`;
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
      + `<div class="lnch-tiles">${r.pages.map(x => `<a href="#" class="lnch-tile" onclick="deptGo('${d.key}',${x.i});return false">${_deptSvg(x.p.icon, 28)}`
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
  document.getElementById('lnchDeptHint').textContent = vis.length > 1
    ? 'แสดงเฉพาะแผนกที่บัญชีนี้มีสิทธิ์เปิดหน้าอย่างน้อยหนึ่งหน้า'
    : 'บัญชีนี้เห็นเฉพาะแผนกของตนเอง';
  document.getElementById('lnchDepts').innerHTML = _lnchDeptsHtml(vis);
  document.getElementById('lnchFoot').innerHTML = _lnchFootHtml();
  updateDeptNav('launcher');
  if (_deptFocus) lnchFocusDept(_deptFocus);
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
function _lnchTabsHtml(f) {
  return _LNCH_TABS.map(([k, t]) => `<button type="button" class="lnch-tab" data-f="${k}" aria-pressed="${f === k}" onclick="lnchTaskFilter('${k}')">${t} <span class="lnch-tab-n">(${_lnchGroups[k].length})</span></button>`).join('');
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
  const mine = todo.items.filter(_shmMine);
  _lnchGroups = { todo: [], review: [], done: [] };
  _LNCH_TABS.forEach(([k]) => { _lnchGroups[k] = _lnchGroup(mine.filter(i => shmState(i) === k)); });
  all.style.display = ''; all.textContent = 'ดูทั้งหมด';
  lnchRenderTasks();
}
function lnchTaskGo(n) { const g = _lnchShown[n]; if (g && g.first.go) { _deptFocus = null; _shmOpen(g.first.go); } }
function lnchTasksAll() { _deptFocus = null; shmFilterSet(_lnchFilter()); supplyGo({ tab: 'supplyhome' }); }

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
