// 16-supplyhome.js — หน้างาน (BoM Center UX overhaul, 2026-10-07): what each person has
// to do now and where the month stands, from GET /api/bom-center/todo; every card
// opens its task through supplyGo (01-core.js) with the month and branch already
// chosen. After a save on any supply page, "ขั้นตอนถัดไป" names the next open item.
// Shares the global scope of slices 01-15 (uses _bmcJson, _bmcChip, _bmcStatus, esc,
// hasModule, tabAllowed, supplyCtx / supplyCtxSet, supplyGo). Every name starts with `shm`.

let _shmData = null;
let _shmToastAt = 0;
const _SHM_ST = { done: ['เสร็จแล้ว', 'var(--good-ink)'], todo: ['รอดำเนินการ', 'var(--amber-ink)'],
                  blocked: ['รอขั้นตอนก่อนหน้า', 'var(--text-muted)'], na: ['ไม่มีงานวันนี้', 'var(--text-muted)'] };
// the status line each save writes → the to-do item it completes (shmAfterStatus)
const _SHM_STATUS_KEY = { bmcIngStatus: 'master_issues', bmcPriceStatus: 'price_month', bmcRecStatus: 'draft',
  bmcPrepStatus: 'sub_recipes', bmcPrepEditStatus: 'sub_recipes', bmcRlStatus: 'checks', bmcCalStatus: 'calendar',
  ohbCenStatus: 'central_report', ohbLinkStatus: 'code_links', ohbDelStatus: 'deliveries', ohbCntStatus: 'begin_count',
  brnStatus: 'count_today', brnOrdStatus: 'order_today_1', brnVarStatus: 'receipts_confirm', brnRecvStatus: 'receive_pending',
  ohbDStatus: 'queue_pending' };

// The วัตถุดิบและใบสั่งซื้อ bar (01-core.js): this tab has one view; a month from a link lands in the strip.
_SUPPLY_PANE.supplyhome = { get: () => null, set: () => {}, show: () => loadSupplyHome(),
  month: m => { const el = document.getElementById('shmMonth'); if (el) el.value = m; } };

// "mine" by role (owner_th names the department), not by module: management holds every
// module and would otherwise own every branch's count; a role without a word falls back to modules
const _SHM_ROLE_OWNERS = { branch: ['สาขา'], salessupport: ['Sales Support', 'ฝ่ายขาย'], analyst: ['ฝ่ายวิเคราะห์'],
  accounting: ['บัญชี'], innovation: ['ครัว'], operation: ['Ops'], management: ['ผู้บริหาร', 'ผู้ดูแลระบบ'] };
function _shmMine(it) {
  const role = window.AUTH && window.AUTH.enabled && window.AUTH.role;
  const words = role && _SHM_ROLE_OWNERS[role];
  if (words && it.owner_th) return words.some(w => it.owner_th.includes(w));
  return (it.owner_modules || []).some(hasModule);
}
function _shmQuery() {
  const qs = new URLSearchParams();
  const m = document.getElementById('shmMonth'), b = document.getElementById('shmBranch');
  const ctx = supplyCtx();
  if (m && m.value) qs.set('month', m.value); else if (ctx.month) qs.set('month', ctx.month);
  if (b && b.options.length) { if (b.value) qs.set('branch_id', b.value); }
  else if (ctx.branch_id && !(window.AUTH && window.AUTH.enabled && window.AUTH.role === 'branch')) qs.set('branch_id', ctx.branch_id);   // a branch login is scoped by the server
  return qs;
}

async function loadSupplyHome() {
  const ctx = supplyCtx();
  const m = document.getElementById('shmMonth');
  if (m && !m.value && ctx.month) m.value = ctx.month;
  _bmcStatus('shmStatus', 'กำลังโหลด…');
  try {
    _shmData = await _bmcJson(`${API}/api/bom-center/todo?${_shmQuery()}`);
  } catch (e) { _bmcStatus('shmStatus', String(e.message || e), 'var(--bad-ink)'); return; }
  const d = _shmData;
  if (m && !m.value) m.value = d.month;
  const b = document.getElementById('shmBranch'), wrap = document.getElementById('shmBranchWrap');
  if (d.scoped) { wrap.style.display = 'none'; }
  else {
    wrap.style.display = '';
    if (!b.options.length) {
      b.innerHTML = '<option value="">ทุกสาขา</option>' + d.branches.map(x => `<option value="${x.branch_id}">${esc(x.name)}</option>`).join('');
      b.value = d.branch ? String(d.branch.branch_id) : '';
    }
  }
  supplyCtxSet({ month: d.month, branch_id: d.branch ? d.branch.branch_id : (d.scoped ? ctx.branch_id : (b && b.value ? Number(b.value) : null)) });
  document.getElementById('shmNote').textContent = `วันนี้ ${d.today} · เวอร์ชันสูตรงวดถัดไป ${d.release.effective_month} · ${d.release.in_force}`;
  _shmRender(d);
  _bmcStatus('shmStatus', '');
}
function shmCtxChanged() {
  const m = document.getElementById('shmMonth'), b = document.getElementById('shmBranch');
  supplyCtxSet({ month: m.value || null, branch_id: b && b.value ? Number(b.value) : null });
  loadSupplyHome();
}

function _shmRender(d) {
  const items = d.items || [];
  const mine = items.filter(_shmMine), others = items.filter(it => !_shmMine(it));
  const order = { todo: 0, blocked: 1, done: 2, na: 3 };
  const sortf = (a, b) => ((b.urgent ? 1 : 0) - (a.urgent ? 1 : 0)) || (order[a.status] - order[b.status]) || (a.seq - b.seq);
  mine.sort(sortf); others.sort(sortf);
  const open = mine.filter(it => it.status === 'todo' || it.status === 'blocked').length;
  document.getElementById('shmMineCount').textContent = mine.length ? `รอดำเนินการ ${open} จาก ${mine.length}` : '';
  document.getElementById('shmMine').innerHTML = mine.length ? mine.map(it => _shmCard(it)).join('')
    : '<div class="text-xs py-2" style="color:var(--text-muted)">ไม่มีงานที่บทบาทของคุณต้องทำในขณะนี้</div>';
  document.getElementById('shmOthersCount').textContent = others.length ? `(${others.filter(it => it.status === 'todo' || it.status === 'blocked').length} รอดำเนินการ จาก ${others.length})` : '(ไม่มี)';
  document.getElementById('shmOthersBody').innerHTML = others.map(it => _shmCard(it)).join('');
  const c = d.close || { steps: [] };
  document.getElementById('shmCloseMonth').textContent = c.month || '';
  document.getElementById('shmCloseCount').textContent = c.steps.length ? `เสร็จ ${c.done} จาก ${c.total} ขั้นตอน` : '';
  document.getElementById('shmClose').innerHTML = c.steps.map(s => _shmCard(s, s.seq)).join('');
}
function _shmCard(it, n) {
  const [lbl, ink] = _SHM_ST[it.status] || _SHM_ST.todo;
  const openable = it.go && it.go.tab && tabAllowed(it.go.tab);   // another department's step shows its owner, not a button
  const act = !openable ? '' : it.status === 'done' || it.status === 'na'
    ? `<a href="#" onclick="shmGo('${esc(it.key)}');return false" class="text-[11px] whitespace-nowrap" style="color:var(--text-muted)">ดู →</a>`
    : `<button onclick="shmGo('${esc(it.key)}')" class="text-xs rounded-lg px-3 py-1 whitespace-nowrap" style="background:var(--accent);color:var(--on-accent);border:0;cursor:pointer">ไปที่งาน →</button>`;
  const dim = it.status === 'done' || it.status === 'na';
  const per = (it.branches || []).length > 1
    ? `<div class="text-[11px] mt-1 flex flex-wrap gap-x-3 gap-y-0.5">${it.branches.map(b => `<span style="color:${(_SHM_ST[b.status] || _SHM_ST.todo)[1]}">${b.status === 'done' ? '✓' : '•'} ${esc(b.branch)}: ${esc(b.detail)}</span>`).join('')}</div>`
    : (it.detail ? `<div class="text-[11px] mt-0.5" style="color:var(--text-secondary)">${esc(it.detail)}</div>` : '');
  return `<div class="flex items-start gap-3 py-2" style="border-top:1px solid var(--border-soft);opacity:${dim ? .75 : 1}">
    ${n ? `<span class="text-xs font-semibold" style="color:var(--text-muted);min-width:18px">${n}.</span>` : ''}
    <div class="flex-1 min-w-0">
      <div class="text-sm" style="color:var(--text-primary)">${esc(it.title)} ${_bmcChip(lbl, it.urgent ? 'var(--bad-ink)' : ink)}${it.count ? ' ' + _bmcChip(`${it.count} รายการ`, ink) : ''}</div>
      ${per}
      ${it.owner_th ? `<div class="text-[11px]" style="color:var(--text-muted)">${esc(it.owner_th)}${it.branch && !(it.branches || []).length ? ' · ' + esc(it.branch) : ''}</div>` : ''}
    </div>${act}</div>`;
}

// ── deep links ──────────────────────────────────────────────────────────────
function _shmFind(key) {
  const d = _shmData || {};
  return (d.items || []).find(i => i.key === key) || ((d.close && d.close.steps) || []).find(s => s.key === key) || null;
}
function shmGo(key) { const it = _shmFind(key); if (it) _shmOpen(it.go); }
function shmGoFromHome(key) {   // the Home page's queue (07-home.js) keeps its own copy of the list
  const it = ((window._shmHome && window._shmHome.items) || []).find(i => i.key === key);
  if (it) _shmOpen(it.go);
}
function _shmWhen(pred, fn, tries) {
  let n = tries || 30;
  const t = setInterval(() => {
    let ok = false;
    try { ok = !!pred(); } catch (e) { ok = false; }
    if (ok) { clearInterval(t); try { fn(); } catch (e) { /* the pane changed under us */ } }
    else if (--n <= 0) clearInterval(t);
  }, 100);
}
// Open a task: the pane, the month and the branch through supplyGo; then what the
// pane itself cannot read from the context (a round, a release, a count date, a period).
function _shmOpen(go) {
  if (!go || !go.tab) return;
  if (!tabAllowed(go.tab)) { notifyError('บทบาทของคุณไม่มีสิทธิ์เปิดหน้านี้'); return; }
  const x = go.extra || {};
  if (x.round && typeof _brnRound !== 'undefined') { _brnRound = x.round; _brnRoundKeep = true; }   // survives the page's branch reset
  if (go.tab === 'settings') { switchTab('settings'); if (go.pane) { try { switchSettingsTab(go.pane); } catch (e) {} } return; }
  supplyGo({ tab: go.tab, pane: go.pane, month: go.month, branch: go.branch, anchor: go.anchor });
  if (x.prob) setTimeout(() => { try { bmcGoIng(x.prob); } catch (e) {} }, 60);
  if (x.release_id) _shmWhen(() => { const s = document.getElementById('bmcRlSel'); return s && s.options.length; }, () => {
    const s = document.getElementById('bmcRlSel');
    if (String(s.value) !== String(x.release_id) && [...s.options].some(o => o.value === String(x.release_id))) { s.value = String(x.release_id); bmcLoadRelease(); }
  });
  if (x.count_date) _shmWhen(() => document.getElementById('ohbCntDate'), () => { document.getElementById('ohbCntDate').value = x.count_date; });
  if (x.order_id) _shmWhen(() => typeof brnOpenReceive === 'function' && _brnMe && document.getElementById('brnRecvOpen'), () => brnOpenReceive(x.order_id));
  if (x.end_count_id) _shmWhen(() => { const s = document.getElementById('brnVarPeriod'); return s && s.options.length; }, () => {
    const s = document.getElementById('brnVarPeriod');
    if (String(s.value) !== String(x.end_count_id) && [...s.options].some(o => o.value === String(x.end_count_id))) { s.value = String(x.end_count_id); brnLoadVariance(x.end_count_id); }
  });
}

// ── "ขั้นตอนถัดไป" after a save ───────────────────────────────────────────────
// _bmcStatus (13-bom-center.js) calls shmAfterStatus(id) whenever a status line
// starts with ✓; the next open item after the one that save completes is shown as
// a toast that opens it. One toast per 2 s — a save may write two status lines.
function shmAfterStatus(id) {
  const k = id === 'brnOrdStatus' && typeof _brnRound !== 'undefined' && _brnRound ? 'order_today_' + _brnRound : _SHM_STATUS_KEY[id];
  if (k) shmAfter(k);
}
async function shmAfter(key) {
  if (!key || Date.now() - _shmToastAt < 2000) return;
  _shmToastAt = Date.now();
  try {
    const d = await _bmcJson(`${API}/api/bom-center/todo?${_shmQuery()}`);
    if (activeTab === 'supplyhome') { _shmData = d; _shmRender(d); }
    const all = [...(d.items || []), ...((d.close && d.close.steps) || [])];
    const me = all.find(i => i.key === key || i.key.startsWith(key));
    const open = all.filter(i => (i.status === 'todo' || i.status === 'blocked') && i !== me && (!me || i.group !== me.group || i.seq > me.seq));
    const nx = open.find(it => _shmMine(it) && (!me || it.group === me.group)) || open.find(_shmMine) || open[0];
    if (nx) _shmToast(nx);
  } catch (e) { /* a hint only */ }
}
function _shmToast(it) {
  document.querySelectorAll('.shm-toast').forEach(e => e.remove());
  const mine = _shmMine(it);
  const el = document.createElement('div');
  el.className = 'shm-toast';
  el.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:60;max-width:360px;background:var(--bg-card);border:1px solid var(--border);border-left:4px solid var(--accent);border-radius:var(--r-md, 10px);box-shadow:0 6px 20px rgba(0,0,0,.12);padding:10px 12px';
  el.innerHTML = `<div class="text-[11px]" style="color:var(--text-muted)">ขั้นตอนถัดไป${mine ? '' : ' (' + esc(it.owner_th || 'ฝ่ายอื่น') + ')'}</div>
    <div class="text-sm" style="color:var(--text-primary)">${esc(it.title)}</div>
    <div class="flex gap-2 mt-2">${mine && it.go && it.go.tab ? `<button onclick="this.closest('.shm-toast').remove();_shmOpen(${esc(JSON.stringify(it.go))})" class="text-xs rounded-lg px-3 py-1" style="background:var(--accent);color:var(--on-accent);border:0;cursor:pointer">ไปที่งาน →</button>` : ''}
      <button onclick="this.closest('.shm-toast').remove()" class="text-xs rounded-lg px-3 py-1" style="border:1px solid var(--border);color:var(--text-secondary);background:transparent;cursor:pointer">ปิด</button></div>`;
  document.body.appendChild(el);
  setTimeout(() => { try { el.remove(); } catch (e) {} }, 12000);
}
