// 16-supplyhome.js — หน้างาน (BoM Center UX overhaul, 2026-10-07): what each person has
// to do now and where the month stands, from GET /api/bom-center/todo; every card
// opens its task through supplyGo (01-core.js) with the month and branch already
// chosen. After a save on any supply page, "ขั้นตอนถัดไป" names the next open item.
// PR-M2 (2026-10-10): each role sees its own work — "งานของฉัน" is the one open block, the month-close list is shown
// only to a role that owns a step (and only its steps), "งานของฝ่ายอื่น" is a folded, capped list for the centre roles.
// Shares the global scope of slices 01-15 (uses _bmcJson, _bmcChip, _bmcStatus, esc,
// hasModule, tabAllowed, supplyCtx / supplyCtxSet, supplyGo). Every name starts with `shm`.

let _shmData = null;
let _shmToastAt = 0;
const _SHM_ST = { done: ['เสร็จแล้ว', 'var(--good-ink)'], todo: ['รอทำ', 'var(--amber-ink)'],
                  blocked: ['รอขั้นตอนก่อนหน้า', 'var(--text-muted)'], na: ['ไม่มีงาน', 'var(--text-muted)'] };
// the status line each save writes → the to-do item it completes (shmAfterStatus)
const _SHM_STATUS_KEY = { bmcIngStatus: 'master_issues', bmcIngSummary: 'master_issues', bmcPriceStatus: 'price_month', bmcRecStatus: 'draft',
  bmcSubStatus: 'price_submit', bmcPrepStatus: 'sub_recipes', bmcPrepEditStatus: 'sub_recipes', bmcRlStatus: 'checks', bmcCalStatus: 'calendar',
  ohbCenStatus: 'central_report', ohbLinkStatus: 'code_links', ohbDelStatus: 'deliveries', ohbCntStatus: 'begin_count',
  brnStatus: 'end_count', brnIssStatus: 'issue_today', brnOrdStatus: 'order_due_1', brnVarStatus: 'receipts_confirm', brnRecvStatus: 'receive_pending',
  ohbDStatus: 'queue_pending' };

// The วัตถุดิบและใบสั่งซื้อ bar (01-core.js): this tab has one view; a month from a link lands in the strip.
_SUPPLY_PANE.supplyhome = { get: () => null, set: () => {}, show: () => loadSupplyHome(),
  month: m => { supplyMonthPut(document.getElementById('shmMonth'), m); } };

// "mine" by role (owner_th names the department), not by module: management holds every
// module and would otherwise own every branch's count; a role without a word falls back to modules
const _SHM_ROLE_OWNERS = { branch: ['สาขา'], salessupport: ['ประสานงานขาย'], analyst: ['ฝ่ายวิเคราะห์'],
  accounting: ['บัญชี'], innovation: ['ครัว'], operation: ['ฝ่ายปฏิบัติการ'], management: ['ผู้บริหาร', 'ผู้ดูแลระบบ'],
  warehouse: ['คลัง'] };
// the roles that see the month-close list (they own a step of it), and the roles that may look at other departments' work
const _SHM_CLOSE_ROLES = ['analyst', 'salessupport', 'accounting', 'management'];
const _SHM_CENTRE_ROLES = ['analyst', 'salessupport', 'management', 'operation'];
function _shmRole() { return (window.AUTH && window.AUTH.enabled && window.AUTH.role) || 'management'; }
function _shmMine(it) {
  const role = window.AUTH && window.AUTH.enabled && window.AUTH.role;
  const words = role && _SHM_ROLE_OWNERS[role];
  if (words && it.owner_th) return words.some(w => it.owner_th.includes(w));
  return (it.owner_modules || []).some(hasModule);
}
// the month หน้างาน starts at when nobody has chosen: this month; a role that works the month being closed (it owns a step of the close
// list) starts at the previous month, which is the month those steps and the price file belong to
function _shmStartsPrev() { return _SHM_CLOSE_ROLES.includes(_shmRole()); }
function _shmQuery() {
  const qs = new URLSearchParams();
  const m = document.getElementById('shmMonth'), b = document.getElementById('shmBranch');
  const ctx = supplyCtx();
  if (m && m.value) qs.set('month', m.value); else if (ctx.month) qs.set('month', ctx.month);
  if (b && b.options.length) { if (b.value) qs.set('branch_id', b.value); }
  else if (ctx.branch_id && !isPinnedSession()) qs.set('branch_id', ctx.branch_id);   // a pinned login is scoped by the server
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
  if (d.scoped || !b) { if (wrap) wrap.style.display = 'none'; }     // a pinned login has no picker element at all (stripBranchPickers)
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
  supplyCtxSet({ branch_id: d.branch ? d.branch.branch_id : (d.scoped ? ctx.branch_id : (b && b.value ? Number(b.value) : null)) });
  // the branch is named ONCE, in the header (a branch login has one); the version of the recipes in force is the analyst's
  // and the manager's line only
  const bn = document.getElementById('shmBranchName');
  const mine = d.scoped && d.branches && d.branches[0];
  if (bn) { bn.textContent = mine ? mine.name : ''; bn.style.display = mine ? '' : 'none'; }
  const role = _shmRole();
  document.getElementById('shmNote').textContent = `วันนี้ ${fmtDay(d.today)}` +
    (role === 'analyst' || role === 'management' ? ` · สูตรเดือนถัดไป ${fmtMonthTh(d.release.effective_month)} · ${d.release.in_force}` : '');
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
  const st = { todo: 0, blocked: 1, done: 2, na: 3 };
  return role === 'branch'
    ? (a, b) => (st[a.status] - st[b.status]) || (_shmRank(a) - _shmRank(b)) || (a.seq - b.seq)
    : (a, b) => ((b.urgent ? 1 : 0) - (a.urgent ? 1 : 0)) || (st[a.status] - st[b.status]) || (a.seq - b.seq);
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
function _shmOthersHtml(others) {
  const open = others.filter(it => it.status === 'todo' || it.status === 'blocked');
  const groups = {}, singles = [];
  open.forEach(it => { const g = _shmGroupKey(it); if (g) (groups[g] = groups[g] || []).push(it); else singles.push(it); });
  const lines = singles.map(it => ({ seq: it.seq, urgent: !!it.urgent, html: _shmCard(it) }));
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
      <div class="flex-1 min-w-0"><div class="text-sm" style="color:var(--text-primary)">${esc(title)} ${_bmcChip(`${names.size} สาขา`, list.some(x => x.urgent) ? 'var(--bad-ink)' : 'var(--amber-ink)')}</div>
        <div class="text-[11px]" style="color:var(--text-muted)">${esc(first.owner_th || '')}</div></div>${act}</div>` });
  });
  lines.sort((a, b) => ((b.urgent ? 1 : 0) - (a.urgent ? 1 : 0)) || (a.seq - b.seq));
  const shown = lines.slice(0, _SHM_OTHERS_CAP);
  return shown.map(l => l.html).join('')
    + (lines.length > shown.length ? `<div class="text-[11px] py-2" style="color:var(--text-muted);border-top:1px solid var(--border-soft)">และอีก ${lines.length - shown.length} เรื่อง</div>` : '')
    + (!lines.length ? '<div class="text-xs py-2" style="color:var(--text-muted)">ไม่มีงานค้าง</div>' : '');
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
  // สั่งสินค้า: the pending round whose cutoff is today and nearest; else "ส่งแล้ว"; else nothing is due today
  const ord = items.filter(it => base(it).startsWith('order_due_'));
  const today = ord.filter(it => it.due_today);
  const pend = today.filter(it => it.status === 'todo').sort((a, b) => String(a.cutoff_at).localeCompare(String(b.cutoff_at)));
  if (pend.length) {
    const it = pend[0];
    const ms = new Date(it.cutoff_at).getTime() - Date.now();
    const left = isNaN(ms) || ms <= 0 ? '' : ` (${fmtLeft(ms / 3600000)})`;
    rows.push({ k: 'order', label: 'สั่งสินค้า', ok: false, bad: !!it.late,
      text: it.late ? 'เลยเวลาตัดรอบ · ส่งตอนนี้มีค่าปรับ' : `ส่งใบสั่งซื้อภายใน ${fmtCutoff(it.cutoff_at)}${left}`, go: it.go });
  } else if (today.length) {
    rows.push({ k: 'order', label: 'สั่งสินค้า', ok: true, text: 'ส่งแล้ว', go: today[0].go });
  } else {
    rows.push({ k: 'order', label: 'สั่งสินค้า', ok: true, text: 'ไม่มีรอบที่ต้องส่งวันนี้', go: (ord[0] && ord[0].go) || { tab: 'branchstock', pane: 'order' } });
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
  card.style.display = role === 'branch' ? '' : 'none';
  if (role !== 'branch') return;
  document.getElementById('shmTodayRows').innerHTML = _shmTodayRows(d).map(r => {
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

function _shmRender(d) {
  const role = _shmRole();
  _shmTodayStrip(d, role);
  shmInstallRefresh();
  const items = d.items || [];
  const c = d.close || { steps: [], branches: [] };
  const steps = c.steps || [];
  const mineItems = items.filter(_shmMine), others = items.filter(it => !_shmMine(it));
  mineItems.sort(_shmSortMine(role));
  const showClose = _SHM_CLOSE_ROLES.includes(role) && steps.length > 0;
  // a role that owns steps of the close sees those; the manager reads the whole list (the whole month is the manager's overview)
  const mineSteps = showClose ? (role === 'management' ? steps.slice() : steps.filter(_shmMine)) : [];
  const stepsAhead = mineSteps.filter(s => s.status === 'todo' || s.status === 'blocked');
  // "งานของฉัน": my open items first, then my open steps of the close (accounting's step 8 is its whole work)
  const mine = mineItems.concat(role === 'management' ? [] : stepsAhead);
  const open = mine.filter(it => it.status === 'todo' || it.status === 'blocked').length;
  document.getElementById('shmMineCount').textContent = mine.length ? `รอทำ ${open} จาก ${mine.length}` : '';
  // what is open is listed; what is finished is one folded line, so the list is what is left to do
  const isOpen = it => it.status === 'todo' || it.status === 'blocked';
  const card = it => _shmCard(it, it.group === 'close' ? it.seq : undefined, true);
  const finished = mine.filter(it => !isOpen(it));
  document.getElementById('shmMine').innerHTML = (open ? mine.filter(isOpen).map(card).join('')
    : '<div class="text-xs py-2" style="color:var(--text-muted)">ไม่มีงานค้าง</div>')
    + (finished.length ? `<details class="mt-1"><summary class="text-[11px] cursor-pointer py-1" style="color:var(--text-muted)">เสร็จแล้ว ${finished.length} รายการ</summary>${finished.map(card).join('')}</details>` : '');
  // the close list: collapsed, only the steps of this role; the rest is one grey line
  const closeCard = document.getElementById('shmCloseCard');
  closeCard.style.display = showClose ? '' : 'none';
  if (showClose) {
    document.getElementById('shmCloseMonth').textContent = fmtMonthTh(c.month || '');
    document.getElementById('shmCloseCount').textContent = ` · เสร็จ ${c.done} จาก ${c.total} ขั้นตอน`;
    const rest = steps.length - mineSteps.length;
    document.getElementById('shmClose').innerHTML = mineSteps.map(s => _shmCard(s, s.seq)).join('')
      + (rest > 0 ? `<div class="text-[11px] py-2" style="color:var(--text-muted);border-top:1px solid var(--border-soft)">ขั้นตอนของฝ่ายอื่น ${rest}</div>` : '');
  }
  // other departments' work: for the centre roles only, folded and capped (never for a branch, the warehouse, the kitchen, accounting)
  const othersCard = document.getElementById('shmOthers');
  const showOthers = _SHM_CENTRE_ROLES.includes(role);
  othersCard.style.display = showOthers ? '' : 'none';
  if (showOthers) {
    const waiting = others.filter(it => it.status === 'todo' || it.status === 'blocked').length;
    document.getElementById('shmOthersCount').textContent = waiting ? `(${waiting} รอทำ)` : '(ไม่มี)';
    document.getElementById('shmOthersBody').innerHTML = _shmOthersHtml(others);
  }
}
function _shmCard(it, n, mineBlock) {
  const [lbl, ink] = _SHM_ST[it.status] || _SHM_ST.todo;
  const openable = it.go && it.go.tab && tabAllowed(it.go.tab);   // another department's step shows its owner, not a button
  const act = !openable ? '' : it.status === 'done' || it.status === 'na'
    ? `<a href="#" onclick="shmGo('${esc(it.key)}');return false" class="text-[11px] whitespace-nowrap" style="color:var(--text-muted)">ดู →</a>`
    : `<button onclick="shmGo('${esc(it.key)}')" class="text-xs rounded-lg px-3 py-1 whitespace-nowrap" style="background:var(--accent);color:var(--on-accent);border:0;cursor:pointer">ไปที่งาน →</button>`;
  const dim = it.status === 'done' || it.status === 'na';
  const per = (it.branches || []).length > 1
    ? `<div class="text-[11px] mt-1 flex flex-wrap gap-x-3 gap-y-0.5">${it.branches.map(b => `<span style="color:${(_SHM_ST[b.status] || _SHM_ST.todo)[1]}">${b.status === 'done' ? '✓' : '•'} ${esc(b.branch)}: ${esc(b.detail)}</span>`).join('')}</div>`
    : (it.detail ? `<div class="text-[11px] mt-0.5" style="color:var(--text-secondary)">${esc(it.detail)}</div>` : '');
  // the branch is written once: in the header of a branch login, so a card of its own list does not repeat it
  const scoped = !!(_shmData && _shmData.scoped);
  const owner = [it.owner_th, it.branch && !(it.branches || []).length && !scoped ? it.branch : ''].filter(Boolean).join(' · ');
  return `<div class="flex items-start gap-3 py-2" style="border-top:1px solid var(--border-soft);opacity:${dim ? .75 : 1}">
    ${n ? `<span class="text-xs font-semibold" style="color:var(--text-muted);min-width:18px">${n}.</span>` : ''}
    <div class="flex-1 min-w-0">
      <div class="text-sm" style="color:var(--text-primary)">${esc(it.title)} ${_bmcChip(lbl, it.urgent ? 'var(--bad-ink)' : ink)}${it.count ? ' ' + _bmcChip(`${it.count} รายการ`, ink) : ''}</div>
      ${per}
      ${owner && !(scoped && mineBlock) ? `<div class="text-[11px]" style="color:var(--text-muted)">${esc(owner)}</div>` : ''}
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
function shmAfterStatus(id) {
  const k = id === 'brnOrdStatus' && typeof _brnRound !== 'undefined' && _brnRound ? 'order_due_' + _brnRound : _SHM_STATUS_KEY[id];
  if (k) shmAfter(k);
}
function _shmToastOk(it) {
  return _shmMine(it) && ((it.owner_modules || []).length === 0 || it.owner_modules.some(hasModule)) && !!(it.go && it.go.tab && tabAllowed(it.go.tab));
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
    const mineOpen = open.filter(_shmToastOk);
    const nx = mineOpen.find(it => !me || it.group === me.group) || mineOpen[0];
    if (nx) _shmToast(nx);
  } catch (e) { /* a hint only */ }
}
function _shmToast(it) {
  document.querySelectorAll('.shm-toast').forEach(e => e.remove());
  const el = document.createElement('div');
  el.className = 'shm-toast';
  el.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:60;max-width:360px;background:var(--bg-card);border:1px solid var(--border);border-left:4px solid var(--accent);border-radius:var(--r-md, 10px);box-shadow:0 6px 20px rgba(0,0,0,.12);padding:10px 12px';
  el.innerHTML = `<div class="text-[11px]" style="color:var(--text-muted)">ขั้นตอนถัดไป</div>
    <div class="text-sm" style="color:var(--text-primary)">${esc(it.title)}</div>
    <div class="flex gap-2 mt-2">${it.go && it.go.tab ? `<button onclick="this.closest('.shm-toast').remove();_shmOpen(${esc(JSON.stringify(it.go))})" class="text-xs rounded-lg px-3 py-1" style="background:var(--accent);color:var(--on-accent);border:0;cursor:pointer">ไปที่งาน →</button>` : ''}
      <button onclick="this.closest('.shm-toast').remove()" class="text-xs rounded-lg px-3 py-1" style="border:1px solid var(--border);color:var(--text-secondary);background:transparent;cursor:pointer">ปิด</button></div>`;
  document.body.appendChild(el);
  setTimeout(() => { try { el.remove(); } catch (e) {} }, 12000);
}
