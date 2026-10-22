// 16-supplyhome.js — หน้างาน (BoM Center UX overhaul, 2026-10-07): what each person has
// to do now and where the month stands, from GET /api/bom-center/todo; every card
// opens its task through supplyGo (01-core.js) with the month and branch already
// chosen. After a save on any supply page, "ขั้นตอนถัดไป" names the next open item.
// PR-M2 (2026-10-10): each role sees its own work — "งานของฉัน" is the one open block, the month-close list is shown
// only to a role that owns a step (and only its steps), "งานของฝ่ายอื่น" is a folded, capped list for the centre roles.
// PR-TF (2026-10-21): every task carries `state` (todo | review | done, derived on the server for the signed-in role); a segmented
// filter above the lists - ทั้งหมด · ยังไม่เสร็จ · รอตรวจ · เสร็จแล้ว - shows one state in every section, default ยังไม่เสร็จ, remembered in
// localStorage `oshinei_task_filter`. The launcher's "งานของฉันวันนี้" (18-launcher.js) reads and writes the same key.
// Shares the global scope of slices 01-15 (uses _bmcJson, _bmcChip, _bmcStatus, esc,
// hasModule, tabAllowed, supplyCtx / supplyCtxSet, supplyGo). Every name starts with `shm`.

let _shmData = null;
let _shmToastAt = 0;
// ── PR-TF · task state and filter ────────────────────────────────────────────────────────────────────────
const _SHM_STATE_TH = { todo: 'ยังไม่เสร็จ', review: 'รอตรวจ', done: 'เสร็จแล้ว' };
// PR-D6: the control is the shared one (uiStateFilter, 01-core.js) - this page offers one more state, รอตรวจ
const _SHM_FILTERS = [['todo', 'ยังไม่เสร็จ'], ['review', 'รอตรวจ'], ['done', 'เสร็จแล้ว'], ['all', 'ทั้งหมด']];
const _SHM_FILTER_KEYS = _SHM_FILTERS.map(f => f[0]);
// the server derives `state` for the viewer's role; an answer without it (an old cache) is read from the status
function shmState(it) {
  if (it && _SHM_STATE_TH[it.state]) return it.state;
  const st = it && it.status;
  return st === 'done' || st === 'na' ? 'done' : (st === 'blocked' ? 'review' : 'todo');
}
function shmFilterGet() { return uiStateFilter.get(_SHM_FILTER_KEYS); }
function shmFilterSet(f) { uiStateFilter.set(f); }
function _shmFilter() { return shmFilterGet(); }
function shmFilter(f) { shmFilterSet(f); if (_shmData) _shmRender(_shmData); }
function _shmStateChip(st) {
  return st === 'todo' ? '' : _bmcChip(_SHM_STATE_TH[st], st === 'done' ? 'var(--good-ink)' : 'var(--amber-ink)');
}
function _shmEmptyHtml(f) { return uiStateFilter.empty(f, 'shmFilter', 'งาน'); }
function _shmFilterBar(counts, f) { uiStateFilter.bar('shmFilter', { counts, f, pick: 'shmFilter', opts: _SHM_FILTERS }); }

// the status line each save writes → the to-do item it completes (shmAfterStatus)
const _SHM_STATUS_KEY = { bmcIngStatus: 'master_issues', bmcIngSummary: 'master_issues', bmcPriceStatus: 'price_month', bmcRecStatus: 'draft',
  bmcSubStatus: 'price_submit', bmcPrepStatus: 'sub_recipes', bmcPrepEditStatus: 'sub_recipes', bmcRlStatus: 'checks', bmcCalStatus: 'calendar',
  ohbCenStatus: 'central_report', ohbLinkStatus: 'code_links', ohbDelStatus: 'deliveries', ohbCntStatus: 'begin_count',
  brnStatus: 'end_count', brnIssStatus: 'issue_today', brnOrdStatus: 'order_due_1', brnVarStatus: 'receipts_confirm', brnRecvStatus: 'receive_pending', brnRecvOrdDone: 'receive_pending',
  ohbDStatus: 'queue_pending' };

// The วัตถุดิบและใบสั่งซื้อ bar (01-core.js): this tab has one view; a month from a link lands in the strip.
_SUPPLY_PANE.supplyhome = { get: () => null, set: () => {}, show: () => loadSupplyHome(),
  month: m => { supplyMonthPut(document.getElementById('shmMonth'), m); } };

// the roles that see the month-close list (they own a step of it), and the roles that may look at other departments' work
const _SHM_CLOSE_ROLES = ['analyst', 'salessupport', 'accounting', 'management'];
const _SHM_CENTRE_ROLES = ['analyst', 'salessupport', 'management', 'operation'];
function _shmRole() { return (window.AUTH && window.AUTH.enabled && window.AUTH.role) || 'management'; }
// the month หน้างาน starts at when nobody has chosen: this month; a role that works the month being closed (it owns a step of the close
// list) starts at the previous month, which is the month those steps and the price file belong to
function _shmStartsPrev() { return _SHM_CLOSE_ROLES.includes(_shmRole()); }
// 4.5 · a pinned branch login and the warehouse and accounting have ONE job on this page: no month and no branch to choose (the month is the
// one their work belongs to - this month, or for accounting the month being closed - and a branch login is scoped by the server)
const _SHM_NO_PICKER_ROLES = ['warehouse', 'accounting'];
function _shmNoPickers() { return isPinnedSession() || _SHM_NO_PICKER_ROLES.includes(_shmRole()); }
function _shmQuery() {
  const qs = new URLSearchParams();
  const m = document.getElementById('shmMonth'), b = document.getElementById('shmBranch');
  const ctx = supplyCtx(), bare = _shmNoPickers();
  if (m && m.value) qs.set('month', m.value); else if (ctx.month && !bare) qs.set('month', ctx.month);
  if (b && b.options.length) { if (b.value) qs.set('branch_id', b.value); }
  else if (ctx.branch_id && !bare) qs.set('branch_id', ctx.branch_id);   // a pinned login is scoped by the server
  return qs;
}

async function loadSupplyHome() {
  const ctx = supplyCtx();
  const m = document.getElementById('shmMonth');
  if (m) supplyMonthPick(m, _shmStartsPrev());
  _bmcStatus('shmStatus', 'กำลังโหลด…');
  try {
    _shmData = await _bmcJson(`${API}/api/bom-center/todo?${_shmQuery()}`);
  } catch (e) { _bmcStatus('shmStatus', String(e.message || e), 'var(--bad-ink)'); return; }
  const d = _shmData;
  if (m && !m.value) supplyMonthPut(m, d.month);
  const b = document.getElementById('shmBranch'), wrap = document.getElementById('shmBranchWrap');
  const bare = _shmNoPickers(), mw = document.getElementById('shmMonthWrap');
  if (mw) mw.style.display = bare ? 'none' : '';
  if (d.scoped || !b || bare) { if (wrap) wrap.style.display = 'none'; }     // a pinned login has no picker element at all (stripBranchPickers)
  else {
    wrap.style.display = '';
    // PR-M5: only the branches that have data in the month are listed
    let rows = d.branches;
    try { rows = await supplyBranchesWithData(d.month); } catch (e) { /* the full list stands */ }
    const cur = b.value || (d.branch ? String(d.branch.branch_id) : '');
    b.innerHTML = supplyBranchOptions(rows, 'ทุกสาขา');
    b.value = rows.some(x => String(x.branch_id) === cur) ? cur : '';
  }
  // the month is written to the shared choice only when a person changes it (shmCtxChanged); the branch follows the page
  if (!bare) supplyCtxSet({ branch_id: d.branch ? d.branch.branch_id : (d.scoped ? ctx.branch_id : (b && b.value ? Number(b.value) : null)) });
  // the branch is named ONCE, in the header (a branch login has one); the version of the recipes in force is the analyst's
  // and the manager's line only
  const bn = document.getElementById('shmBranchName');
  const mine = d.scoped && d.branches && d.branches[0];
  if (bn) { bn.textContent = mine ? mine.name : ''; bn.style.display = mine ? '' : 'none'; }
  const role = _shmRole();
  document.getElementById('shmNote').textContent = `วันนี้ ${fmtDay(d.today)}` +
    (BC_MODE !== 'stock' && (role === 'analyst' || role === 'management') ? ` · สูตรเดือนถัดไป ${fmtMonthTh(d.release.effective_month)} · ${d.release.in_force}` : '');
  _shmRender(d);
  _bmcStatus('shmStatus', '');
}
function shmCtxChanged() {
  const m = document.getElementById('shmMonth'), b = document.getElementById('shmBranch');
  supplyCtxSet({ month: m.value || null, branch_id: b && b.value ? Number(b.value) : null });
  loadSupplyHome();
}

// A branch login reads its list by urgency: an order past its cutoff, then today's duties, then what is coming, and the days
// it left behind last. Other roles keep the order of the centre's list (urgent first, then the step order).
function _shmRank(it) {
  const k = String(it.key || '').split(':')[0];                       // a per-branch key ends in ":<branch id>"
  if (it.late && !k.startsWith('issue_')) return 0;                  // an order that is past its cutoff
  if (k.startsWith('issue_missing_')) return 3;                       // a day left behind
  if (k === 'issue_today' || it.due_today) return 1;                  // today's duty
  return 2;
}
function _shmSortMine(role) {
  const st = { todo: 0, review: 1, done: 2 };
  return role === 'branch'
    ? (a, b) => (st[shmState(a)] - st[shmState(b)]) || (_shmRank(a) - _shmRank(b)) || (a.seq - b.seq)
    : (a, b) => ((b.urgent ? 1 : 0) - (a.urgent ? 1 : 0)) || (st[shmState(a)] - st[shmState(b)]) || (a.seq - b.seq);
}
// other departments' per-branch duties are one line each, not one card per branch and day
const _SHM_GROUP_TITLE = { slip: 'สาขาที่ยังไม่ยืนยันใบตัดเบิก', order_due: 'สาขาที่ยังไม่ส่งใบสั่งซื้อ',
  receive_pending: 'สาขาที่ยังไม่บันทึกรับสินค้า', variance_open: 'สาขาที่ยังไม่ระบุเหตุผลผลต่าง' };
function _shmGroupKey(it) {
  if (!it.branch_id) return null;
  const k = String(it.key).split(':')[0];
  if (k === 'issue_today' || k.startsWith('issue_missing_')) return 'slip';
  return k.replace(/_(1|2|salmon)$/, '');
}
const _SHM_OTHERS_CAP = 6;
// the lines of other departments' work, one per task - or one per duty for the open per-branch ones; a finished task is its own line
function _shmOthersLines(items) {
  const groups = {}, singles = [];
  items.forEach(it => { const g = shmState(it) === 'done' ? null : _shmGroupKey(it); if (g) (groups[g] = groups[g] || []).push(it); else singles.push(it); });
  const lines = singles.map(it => ({ seq: it.seq, urgent: !!it.urgent && shmState(it) !== 'done', html: _shmCard(it) }));
  Object.entries(groups).forEach(([g, list]) => {
    const names = new Set(list.map(x => x.branch_id));
    const first = list[0];
    // the slip duty opens the centre's list of slips; any other group opens its first item
    const go = g === 'slip' ? { tab: 'orderhub', pane: 'issues' } : first.go;
    const hit = go && go.tab ? _supplyItemOf(go.tab, go.pane) : null;
    const openable = go && go.tab && tabAllowed(go.tab) && (g !== 'slip' || (hit && _supplyItemAllowed(hit.item)));
    const title = _SHM_GROUP_TITLE[g] || first.title;
    const act = openable ? `<a href="#" onclick="_shmOpen(${esc(JSON.stringify(go))});return false" class="text-[11px] whitespace-nowrap" style="color:var(--accent)">ดูรายการ →</a>` : '';
    lines.push({ seq: first.seq, urgent: list.some(x => x.urgent), html: `<div class="flex items-start gap-3 py-2" style="border-top:1px solid var(--border-soft)">
      <div class="flex-1 min-w-0"><div class="text-sm" style="color:var(--text-primary)">${esc(title)} ${_bmcChip(`${names.size} สาขา`, list.some(x => x.urgent) ? 'var(--bad-ink)' : 'var(--amber-ink)')}${shmState(first) === 'review' ? ' ' + _shmStateChip('review') : ''}</div>
        <div class="text-[11px]" style="color:var(--text-muted)">${esc(first.owner_th || '')}</div></div>${act}</div>` });
  });
  lines.sort((a, b) => ((b.urgent ? 1 : 0) - (a.urgent ? 1 : 0)) || (a.seq - b.seq));
  return lines;
}
function _shmOthersHtml(lines, f) {
  const shown = lines.slice(0, _SHM_OTHERS_CAP);
  return shown.map(l => l.html).join('')
    + (lines.length > shown.length ? `<div class="text-[11px] py-2" style="color:var(--text-muted);border-top:1px solid var(--border-soft)">และอีก ${lines.length - shown.length} เรื่อง</div>` : '')
    + (!lines.length ? _shmEmptyHtml(f) : '');
}

// ── PR-M7 · "วันนี้เหลือทำอะไร" (a branch login, หน้างาน only) ──────────────────────────────────────────────
// Three lines — ตัดเบิก · สั่งสินค้า · รับสินค้า — each with a ✓ (nothing left) or ● (something to do) and a link to the task. The
// statuses are read from the same to-do items as the cards below (issue_today, the nearest order_due_*, receive_pending).
const _SHM_WD = ['อา.', 'จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.'];
function _shmTodayRows(d) {
  const items = d.items || [];
  const base = it => String(it.key || '').split(':')[0];
  const rows = [];
  // ตัดเบิก
  const iss = items.find(it => base(it) === 'issue_today');
  const gaps = items.filter(it => base(it).startsWith('issue_missing_') && it.status === 'todo').length;
  if (iss) {
    const ok = iss.status === 'done';
    // 5.4: ONE status for today's slip (ยืนยันแล้ว · ร่าง · ยังไม่ทำ), then the backlog as a count
    const txt = ok ? `ยืนยันแล้ว ${iss.confirmed_hhmm || ''}`.trim() + (iss.appends ? ` · เบิกเพิ่ม ${iss.appends} ครั้ง` : '')
      : (iss.slip_status === 'draft' ? 'ร่าง' : 'ยังไม่ทำ');
    rows.push({ k: 'issue', label: 'ตัดเบิกวันนี้', ok: ok && !gaps, text: txt + (gaps ? ` · ค้าง ${gaps} วัน` : ''), go: iss.go });
  }
  // สั่งสินค้า: the NEXT order due - the open round whose cutoff comes first (a round past its cutoff first of all). "ส่งแล้ว" only when
  // no round is waiting: while another order is still open the strip names it, never a ✓ for the one that went (2.3)
  const ord = items.filter(it => base(it).startsWith('order_due_'));
  const pend = ord.filter(it => it.status === 'todo' && !it.upcoming).sort((a, b) => (b.late ? 1 : 0) - (a.late ? 1 : 0) || String(a.cutoff_at).localeCompare(String(b.cutoff_at)));
  if (pend.length) {
    const it = pend[0];
    const ms = new Date(it.cutoff_at).getTime() - Date.now();
    const left = isNaN(ms) || ms <= 0 ? '' : ` (${fmtLeft(ms / 3600000)})`;
    const more = pend.length > 1 ? ` · รออีก ${pend.length - 1} รอบ` : '';
    rows.push({ k: 'order', label: 'สั่งสินค้า', ok: false, bad: !!it.late,
      text: (it.late ? 'เลยเวลาตัดรอบ · ส่งตอนนี้มีค่าปรับ' : `${it.round_th === 'แซลมอนสด' ? 'แซลมอนสด · ' : ''}รอบส่ง ${fmtDayShort(it.delivery)} · ส่งใบสั่งซื้อภายใน ${fmtCutoff(it.cutoff_at)}${left}`) + more, go: it.go });
  } else if (ord.some(it => it.upcoming)) {           // PR-U1: the next cutoff is on a later day - nothing to do today
    const nx = ord.filter(it => it.upcoming).sort((a, b) => String(a.cutoff_at).localeCompare(String(b.cutoff_at)))[0];
    rows.push({ k: 'order', label: 'สั่งสินค้า', ok: true, text: nx.next_th || 'ยังไม่ถึงรอบตัด', go: nx.go });
  } else if (ord.length) {
    rows.push({ k: 'order', label: 'สั่งสินค้า', ok: true, text: 'ส่งแล้ว', go: ord[0].go });
  } else {
    rows.push({ k: 'order', label: 'สั่งสินค้า', ok: true, text: 'ไม่มีรอบที่ต้องส่ง', go: { tab: 'branchstock', pane: 'order' } });
  }
  // รับสินค้า
  const rec = items.find(it => base(it) === 'receive_pending');
  rows.push(rec ? { k: 'recv', label: 'รับสินค้า', ok: false, text: `มีใบรอรับ ${rec.count || 1} ใบ`, go: rec.go }
    : { k: 'recv', label: 'รับสินค้า', ok: true, text: 'ไม่มีใบรอรับ', go: { tab: 'branchstock', pane: 'recv' } });
  return rows;
}
function _shmTodayStrip(d, role) {
  const card = document.getElementById('shmToday');
  if (!card) return;
  // PR-D6: only what is still open - a line that is finished ("รับสินค้า: ไม่มีใบรอรับ ✓") disappears, and so does the strip when nothing is open
  const open = role === 'branch' ? _shmTodayRows(d).filter(r => !r.ok) : [];
  card.style.display = open.length ? '' : 'none';
  if (!open.length) return;
  document.getElementById('shmTodayRows').innerHTML = open.map(r => {
    const ink = r.ok ? 'var(--good-ink)' : (r.bad ? 'var(--bad-ink)' : 'var(--amber-ink)');
    return `<div class="shm-today-row flex items-center gap-2" data-k="${r.k}" style="border-top:1px solid var(--border-soft);min-height:44px">
      <span aria-hidden="true" style="color:${ink};font-size:16px;width:18px;text-align:center">${r.ok ? '✓' : '●'}</span>
      <div class="flex-1 min-w-0 text-sm" style="color:var(--text-primary)"><b>${r.label}</b>: <span style="color:${ink}">${esc(r.text)}</span></div>
      <a href="#" onclick="_shmOpen(${esc(JSON.stringify(r.go))});return false" class="text-xs whitespace-nowrap" style="color:var(--accent);padding:12px 0 12px 6px">ไปที่งาน →</a></div>`;
  }).join('');
}

// ── PR-M7 · ติดตั้งเป็นแอป (a branch login): reuses the install event and helpers of the owner's phone summary (09c-mobile.js) ──
const _SHM_INSTALL_KEY = 'oshinei_brn_install_dismiss';
function _shmInstallSnoozed() { try { return Date.now() < (+localStorage.getItem(_SHM_INSTALL_KEY) || 0); } catch (e) { return false; } }
function _shmIosSafari() { const ua = navigator.userAgent || ''; return /iPhone|iPad|iPod/.test(ua) && /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua); }
function shmInstallRefresh() {
  const w = document.getElementById('shmInstall');
  if (!w) return;
  const standalone = typeof _msStandalone === 'function' && _msStandalone();
  const evt = typeof _installEvt !== 'undefined' && _installEvt;
  const show = _shmRole() === 'branch' && !standalone && !_shmInstallSnoozed() && (!!evt || _shmIosSafari());
  w.style.display = show ? '' : 'none';
  if (!show) return;
  const btn = document.getElementById('shmInstallBtn'), hint = document.getElementById('shmInstallHint');
  if (evt) { btn.style.display = ''; hint.textContent = 'เปิดได้เหมือนแอป ไม่ต้องพิมพ์ที่อยู่เว็บ'; }
  else { btn.style.display = 'none'; hint.textContent = 'กดปุ่มแชร์ แล้วเลือก “เพิ่มไปยังหน้าจอโฮม”'; }
}
async function shmInstallApp() {
  if (typeof msInstallApp === 'function') await msInstallApp();
  shmInstallRefresh();
}
function shmInstallDismiss() {
  try { localStorage.setItem(_SHM_INSTALL_KEY, String(Date.now() + 30 * 86400000)); } catch (e) { /* the card then returns on the next visit */ }
  shmInstallRefresh();
}

// ── batch 3 · 2.1 รับการแจ้งเตือนบนมือถือ: the push card of this page, for the logins that receive kind "supply" (a branch account,
// the warehouse, Sales Support, Operations, Accounting). The functions are the owner's phone summary ones (09c-mobile.js, prefix shmPush).
const _SHM_PUSH_ROLES = ['branch', 'warehouse', 'salessupport', 'operation', 'accounting'];
async function shmPushInit() {
  const card = document.getElementById('shmPush');
  if (!card) return;
  const hide = () => { card.style.display = 'none'; };
  if (!(window.AUTH && window.AUTH.enabled) || !_SHM_PUSH_ROLES.includes(_shmRole())) return hide();
  if (typeof _msPushSupported !== 'function' || !_msPushSupported()) return hide();
  if (!await msPushVapid()) return hide();
  card.style.display = '';
  await msPushRender(undefined, 'shmPush');
}
// ── batch 3 · 4.7 the all-branches grid as one line (the centre roles; the pane is การใช้งานทุกสาขา under ใบสั่งซื้อ) ──
const _SHM_LIGHT = { green: ['เขียว', 'var(--good-ink)'], yellow: ['เหลือง', 'var(--amber-ink)'], red: ['แดง', 'var(--bad-ink)'],
  not_started: ['ยังไม่เริ่มใช้ระบบ', 'var(--text-muted)'] };
async function shmAdoptionLoad(role) {
  const card = document.getElementById('shmAdoption'), line = document.getElementById('shmAdoptionLine');
  if (!card || !line) return;
  const hit = _supplyItemOf('orderhub', 'adoption');
  const show = !!(hit && _supplyItemAllowed(hit.item)) && _SHM_CENTRE_ROLES.includes(role);
  card.style.display = show ? '' : 'none';
  if (!show) return;
  try {
    const g = await _bmcJson(`${API}/api/bom-center/hub/adoption`);
    line.innerHTML = `ทั้งหมด ${g.total} สาขา ` + ['green', 'yellow', 'red', 'not_started'].filter(k => k !== 'not_started' || g.counts[k]).map(k => _bmcChip(`${_SHM_LIGHT[k][0]} ${g.counts[k]}`, _SHM_LIGHT[k][1])).join(' ');
  } catch (e) { line.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e).slice(0, 120))}</span>`; }
}

function _shmRender(d) {
  const role = _shmRole();
  _shmTodayStrip(d, role);
  shmInstallRefresh();
  shmPushInit();
  shmAdoptionLoad(role);
  // PR-U1: a round whose cutoff is still to come is not a task of today (it is the "ถัดไป" line); a todo task of mine is kept only when the
  // server counts it (mine_open, the badges' rule), so "ยังไม่เสร็จ" here is the launcher's and the badges' number
  const upcoming = (d.items || []).filter(it => it.upcoming);
  const items = (d.items || []).filter(it => !it.upcoming);
  const keys = shmOpenKeys(d), mineOk = it => shmState(it) !== 'todo' || shmIsOpenTask(it, keys);
  const c = d.close || { steps: [], branches: [] };
  const steps = c.steps || [];
  const f = _shmFilter(), keep = it => f === 'all' || shmState(it) === f;
  const mineItems = _shmCollapseBacklog(items.filter(_shmMine)).filter(mineOk), others = items.filter(it => !_shmMine(it));
  const showClose = _SHM_CLOSE_ROLES.includes(role) && steps.length > 0;
  // a role that owns steps of the close sees those; the manager reads the whole list (the whole month is the manager's overview)
  const mineSteps = showClose ? (role === 'management' ? steps.slice() : steps.filter(_shmMine)) : [];
  const stepsAhead = mineSteps.filter(s => (s.status === 'todo' || s.status === 'blocked') && mineOk(s));
  // "งานของฉัน": my items, then my open steps of the close (accounting's step 8 is its whole work); a finished step is in the close list only
  const mine = mineItems.concat(role === 'management' ? [] : stepsAhead);
  const showOthers = _SHM_CENTRE_ROLES.includes(role);
  // the counts of the segmented control: the person's OWN rows, once (a step in "งานของฉัน" and in the close list is one); other departments'
  // work has its own count line - PR-U1: so "ยังไม่เสร็จ (N)" is the same N as the launcher and the badges
  const rows = new Set(mine.concat(role === 'management' ? [] : mineSteps.filter(mineOk)));
  const counts = { all: 0, todo: 0, review: 0, done: 0 };
  rows.forEach(it => { counts[shmState(it)]++; });
  counts.all = counts.todo + counts.review + counts.done;
  _shmFilterBar(counts, f);
  // the open (my move) first, then what waits for a review, then the finished; one list for every filter
  const card = it => _shmCard(it, it.group === 'close' ? it.seq : undefined, true);
  const shownMine = mine.filter(keep).sort(_shmSortMine(role));
  document.getElementById('shmMineCount').textContent = mine.length ? `ยังไม่เสร็จ ${counts.todo}` : '';
  // the next round of a branch whose cutoff is still to come: one muted line, never a card or a count
  const nextLine = role === 'branch' && f !== 'done' ? upcoming.filter(_shmMine).sort((a, b) => String(a.cutoff_at).localeCompare(String(b.cutoff_at))).slice(0, 1)
    .map(it => `<div class="text-xs py-2" style="color:var(--text-muted);border-top:1px solid var(--border-soft)">${esc(it.next_th || it.title)}</div>`).join('') : '';
  document.getElementById('shmMine').innerHTML = (shownMine.length ? shownMine.map(card).join('') : _shmEmptyHtml(f)) + nextLine;
  // the close list: collapsed, only the steps of this role; the rest is one grey line
  const closeCard = document.getElementById('shmCloseCard');
  closeCard.style.display = showClose ? '' : 'none';
  if (showClose) {
    document.getElementById('shmCloseMonth').textContent = fmtMonthTh(c.month || '');
    document.getElementById('shmCloseCount').textContent = ` · เสร็จ ${c.done} จาก ${c.total} ขั้นตอน`;
    const rest = steps.length - mineSteps.length, shownSteps = mineSteps.filter(keep);
    document.getElementById('shmClose').innerHTML = (shownSteps.length ? shownSteps.map(s => _shmCard(s, s.seq)).join('') : _shmEmptyHtml(f))
      + (rest > 0 ? `<div class="text-[11px] py-2" style="color:var(--text-muted);border-top:1px solid var(--border-soft)">ขั้นตอนของฝ่ายอื่น ${rest}</div>` : '');
  }
  // other departments' work: for the centre roles only, folded and capped (never for a branch, the warehouse, the kitchen, accounting)
  const othersCard = document.getElementById('shmOthers');
  othersCard.style.display = showOthers ? '' : 'none';
  if (showOthers) {
    const nTodo = others.filter(it => shmState(it) === 'todo').length, nReview = others.filter(it => shmState(it) === 'review').length;
    document.getElementById('shmOthersCount').textContent = nTodo || nReview
      ? `(${[nTodo ? `${nTodo} ยังไม่เสร็จ` : '', nReview ? `${nReview} รอตรวจ` : ''].filter(Boolean).join(' · ')})` : '(ไม่มี)';
    const lines = [];
    ['todo', 'review', 'done'].filter(k => f === 'all' || f === k).forEach(k => lines.push(..._shmOthersLines(others.filter(it => shmState(it) === k))));
    document.getElementById('shmOthersBody').innerHTML = _shmOthersHtml(lines, f);
  }
}
// 2.3 · the days a branch left behind are ONE row - "ใบตัดเบิกค้าง 4 วัน (8–11/10)" - not a card each. The row keeps the key of the oldest day,
// so its button opens that day's slip. `_brnIssRuns` (14-branch.js) writes the days as ranges.
function _shmCollapseBacklog(items) {
  const isGap = it => String(it.key || '').split(':')[0].startsWith('issue_missing_') && it.status === 'todo';
  const gaps = items.filter(isGap);
  if (!gaps.length) return items;
  const oldest = gaps.slice().sort((a, b) => String(a.slip_date).localeCompare(String(b.slip_date)))[0];
  const row = Object.assign({}, oldest, { title: `ใบตัดเบิกค้าง ${gaps.length} วัน (${_brnIssRuns(gaps.map(g => g.slip_date).filter(Boolean))})`,
    detail: 'ทำภายหลังได้ · เริ่มจากวันที่ค้างนานที่สุด', count: null });
  return items.filter(it => !isGap(it) || it === oldest).map(it => (it === oldest ? row : it));
}
function _shmCard(it, n, mineBlock) {
  const stt = shmState(it);
  const openable = it.go && it.go.tab && tabAllowed(it.go.tab);   // another department's step shows its owner, not a button
  const act = !openable ? '' : stt === 'done'
    ? `<a href="#" onclick="shmGo('${esc(it.key)}');return false" class="text-[11px] whitespace-nowrap" style="color:var(--text-muted)">ดู →</a>`
    : `<button onclick="shmGo('${esc(it.key)}')" class="text-xs rounded-lg px-3 py-1 whitespace-nowrap" style="background:var(--accent);color:var(--on-accent);border:0;cursor:pointer">ไปที่งาน →</button>`;
  const dim = stt === 'done';
  const per = (it.branches || []).length > 1
    ? bmcBranchTable(it.branches)
    : (it.detail ? `<div class="text-[11px] mt-0.5 shm-detail" title="${esc(it.detail)}" style="color:var(--text-secondary);display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden">${esc(it.detail)}</div>` : '');
  // 1.5 · the card "ข้อมูลตั้งต้นที่ยังว่าง": one link per blank field, with the items that are bought for the most ฿ named beside it
  const gp = (it.gaps || []).length ? `<div class="wt-wrap mt-2"><table class="wt"><thead><tr><th>ข้อมูลที่ยังว่าง</th><th class="wt-num">รายการ</th><th>ซื้อมากที่สุด</th><th></th></tr></thead><tbody>${it.gaps.map(g => `<tr>
      <td data-l="ข้อมูลที่ยังว่าง" style="font-weight:600">${esc(g.label)}</td><td class="wt-num" data-l="รายการ">${g.count}</td>
      <td data-l="ซื้อมากที่สุด" style="color:var(--text-secondary)">${bmcLines((g.items || []).slice(0, 3).map(i => esc(i.name)))}</td>
      <td class="wt-act"><button type="button" class="wt-btn" onclick="_shmOpen(${esc(JSON.stringify(g.go))})">ไปแก้ →</button></td></tr>`).join('')}</tbody></table></div>` : '';
  // 3.5 · the data-quality backlog: the top 10 by the plates each item touches
  const bk = (it.backlog || []).length ? `<div class="mt-1">${bmcBacklogHtml({ top: it.backlog, total: it.backlog_total, note: 'จานที่กระทบคิดจากยอดขาย 30 วันล่าสุดของทุกสาขา แก้รายการบนสุดก่อน' }, 10)}</div>` : '';
  // 2.6 · a task made from the variance reasons is closed by "รับทราบ" (or when the cause stops)
  const fb = it.feedback_key && (it.ack_modules || []).some(hasModule)
    ? `<button onclick="shmFbAck('${esc(it.rule)}', ${esc(JSON.stringify(it.subject))}, '${esc(it.month)}')" class="text-xs rounded-lg px-3 py-1 whitespace-nowrap" style="border:1px solid var(--border);color:var(--text-primary);background:transparent;cursor:pointer;min-height:36px">รับทราบแล้ว</button>` : '';
  // the branch is written once: in the header of a branch login, so a card of its own list does not repeat it
  const scoped = !!(_shmData && _shmData.scoped);
  // PR-TF (owner 2026-10-21): minimal text - one detail line; in "งานของฉัน" the department is not repeated (it is mine), only a branch is named
  const owner = [mineBlock ? '' : it.owner_th, it.branch && !(it.branches || []).length && !scoped ? it.branch : ''].filter(Boolean).join(' · ');
  const ink = stt === 'done' ? 'var(--good-ink)' : 'var(--amber-ink)';
  return `<div class="flex items-start gap-3 py-2 shm-card" data-state="${stt}" style="border-top:1px solid var(--border-soft);opacity:${dim ? .75 : 1}">
    ${n ? `<span class="text-xs font-semibold" style="color:var(--text-muted);min-width:18px">${n}.</span>` : ''}
    <div class="flex-1 min-w-0">
      <div class="text-sm" style="color:var(--text-primary)">${esc(it.title)}${it.urgent && stt !== 'done' ? ' ' + _bmcChip('เร่งด่วน', 'var(--bad-ink)') : ''}${stt !== 'todo' ? ' ' + _shmStateChip(stt) : ''}${it.count ? ' ' + _bmcChip(`${it.count} รายการ`, ink) : ''}</div>
      ${per}${gp}${bk}
      ${owner && !(scoped && mineBlock) ? `<div class="text-[11px]" style="color:var(--text-muted)">${esc(owner)}</div>` : ''}
    </div><div class="flex flex-col gap-1 items-end">${act}${fb}</div></div>`;
}

async function shmFbAck(rule, subject, month) {
  if (!await uiConfirm('รับทราบงานนี้แล้ว?\nงานจะหายจากรายการ และกลับมาเมื่อขึ้นเดือนใหม่หากเหตุผลยังซ้ำ', { title: 'รับทราบงาน', ok: 'รับทราบ', cancel: 'ยังไม่รับทราบ' })) return;
  try {
    await _bmcJson(`${API}/api/bom-center/feedback/ack`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rule, subject, month }) });
    loadSupplyHome();
  } catch (e) { notifyError(String(e.message || e)); }
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
  if (x.round && typeof _brnRound !== 'undefined') {
    _brnRound = x.round; _brnRoundKeep = true;                         // survives the page's branch reset
    if (x.delivery) { _brnDelivery = x.delivery; _brnDeliveryKeep = true; }   // the delivery day the task names
  }
  if (x.date && go.pane === 'issue' && typeof _brnIssDate !== 'undefined') { _brnIssDate = x.date; _brnIssDateKeep = true; }   // the day of the slip the task names
  if (go.pane === 'var' && typeof _brnVarTab !== 'undefined') {           // the variance page's view (period / daily / recon) the task names
    if (x.var_tab) _brnVarTab = x.var_tab;
    else if (x.end_count_id) _brnVarTab = 'period';                       // the receipts step opens the period view
    if (x.day) { _brnVarTab = 'daily'; _shmWhen(() => typeof _brnVarDay !== 'undefined' && _brnVarDay, () => brnVarDayOpen(x.day)); }
  }
  if (x.day && go.pane === 'issues') _shmWhen(() => document.getElementById('ohbIsFrom') && typeof ohbLoadIssues === 'function', () => {   // the day the task names, all branches
    document.getElementById('ohbIsFrom').value = x.day; document.getElementById('ohbIsTo').value = x.day; ohbLoadIssues();
  });
  if (x.day && go.pane === 'issuevar') _shmWhen(() => typeof _ohbIv !== 'undefined' && _ohbIv, () => ohbIvOpen(x.day));
  if (go.tab === 'settings') { switchTab('settings'); if (go.pane) { try { switchSettingsTab(go.pane); } catch (e) {} } return; }
  supplyGo({ tab: go.tab, pane: go.pane, month: go.month, branch: go.branch, anchor: go.anchor });
  if (x.prob) setTimeout(() => { try { bmcGoIng(x.prob); } catch (e) {} }, 60);
  if (x.item && go.pane === 'rcheck') { window._bmcRcItem = x.item; _shmWhen(() => typeof bmcRenderRcheck === 'function' && typeof _bmcRc !== 'undefined' && _bmcRc, () => bmcRenderRcheck()); }
  if (x.gap) setTimeout(() => { try { bmcGoIngGap(x.gap); } catch (e) {} }, 60);
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
// PR-M2: only a step of the person's own work, whose page the person's role holds — never another department's task.
function shmAfterStatus(id, toast) {
  const k = id === 'brnOrdStatus' && typeof _brnRound !== 'undefined' && _brnRound ? 'order_due_' + _brnRound : _SHM_STATUS_KEY[id];
  if (k) shmAfter(k, toast);
}
function _shmToastOk(it) {
  return _shmMine(it) && ((it.owner_modules || []).length === 0 || it.owner_modules.some(hasModule)) && !!(it.go && it.go.tab && tabAllowed(it.go.tab));
}
async function shmAfter(key, toast) {
  if (!key || Date.now() - _shmToastAt < 2000) return;
  _shmToastAt = Date.now();
  try {
    const d = await _bmcJson(`${API}/api/bom-center/todo?${_shmQuery()}`);
    if (activeTab === 'supplyhome') { _shmData = d; _shmRender(d); }
    const all = [...(d.items || []), ...((d.close && d.close.steps) || [])];
    const me = all.find(i => i.key === key || i.key.startsWith(key));
    const open = all.filter(i => (i.status === 'todo' || i.status === 'blocked') && !i.upcoming && i !== me && (!me || i.group !== me.group || i.seq > me.seq));
    const mineOpen = open.filter(_shmToastOk);
    const nx = mineOpen.find(it => !me || it.group === me.group) || mineOpen[0];
    if (nx) _shmToast(nx, toast);
  } catch (e) { /* a hint only */ }
}
// PR-D6: the next step is a line + a button INSIDE the success toast of the save (uiToast, 01-core.js); a toast that has already closed gets a new one
function _shmToast(it, toast) {
  const go = it.go && it.go.tab ? it.go : null;
  const text = 'ขั้นตอนถัดไป: ' + it.title;
  if (toast && toast.el && document.body.contains(toast.el)) toast.next(text, go ? 'ไปที่งาน →' : '', go ? () => _shmOpen(go) : null);
  else uiToast(text, 'info', go ? { action: { label: 'ไปที่งาน →', onClick: () => _shmOpen(go) } } : {});
}
