// 15-orderhub.js — ศูนย์สั่งของ (BoM Center Phase 2 · S9): Sales Support's order queue,
// Ops' ordering compliance and the monthly usage-variance summary. Shares the global
// scope of slices 01-14 (uses _bmcJson / _bmcChip / _bmcNum / _bmcStatus / _bmcQ from
// 13); every name here starts with `ohb`.

let _ohbSub = 'queue';
let _ohbStatus = 'submitted';
// The วัตถุดิบและใบสั่งซื้อ bar (01-core.js) reads, sets and shows this tab's panes through here.
_SUPPLY_PANE.orderhub = {
  get: () => _ohbSub, set: k => { _ohbSub = k; }, show: k => ohbSetSub(k),
  month: m => { supplyMonthPut(document.getElementById('ohbMonth'), m); },
};
let _ohbQ = null;             // /api/bom-center/hub/queue
let _ohbOrder = null;         // the order open in the detail panel
const _OHB_RND = { '1': 'รอบ 1', '2': 'รอบ 2', salmon: 'แซลมอนสด' };
const _OHB_ST = [['submitted', 'รอยืนยันยอดส่ง'], ['processed', 'คลังจัดส่งแล้ว'], ['received', 'สาขารับแล้ว'], ['unkeyed', 'ยังไม่คีย์บัญชี'], ['all', 'ทั้งหมด']];
// PR-M5: the warehouse sees three filters — what to confirm, what it has sent, everything; สาขารับแล้ว and ยังไม่คีย์บัญชี are the centre's
const _OHB_ST_WH = [['submitted', 'รอยืนยันยอดส่ง'], ['processed', 'คลังจัดส่งแล้ว'], ['all', 'ทั้งหมด']];
function _ohbIsWarehouse() { return !!(window.AUTH && window.AUTH.enabled && window.AUTH.role === 'warehouse'); }
function _ohbStList() { return _ohbIsWarehouse() ? _OHB_ST_WH : _OHB_ST; }
// a phone (the branch login's strip or any narrow screen): the queue is cards and a line is a stacked block
function _ohbPhone() { return document.body.classList.contains('supply-phone') || window.matchMedia('(max-width: 700px)').matches; }
let _ohbWasPhone = null;
window.addEventListener('resize', () => {
  const p = _ohbPhone();
  if (_ohbWasPhone === null || p === _ohbWasPhone) return;
  _ohbWasPhone = p;
  if (typeof activeTab !== 'undefined' && activeTab === 'orderhub' && _ohbSub === 'queue' && _ohbQ) { ohbRenderQueueList(); if (_ohbOrder && document.getElementById('ohbDetail').style.display !== 'none') ohbRenderDetail(); }
});
// owner 2026-10-09: Sales Support keys the processed orders into the accounting system and ticks them here
const _ohbKeyedSel = new Set();      // order ids ticked in the queue
function _ohbCanKey() { return hasModule('supply.ordering'); }
function _ohbKeyable(o) { return ['processed', 'received'].includes(o.status); }
// owner 2026-10-08: the WAREHOUSE updates what is sent (the queue's ส่งจริง + ดำเนินการแล้ว); Sales Support decides claims
function _ohbCanWork() { return hasModule('supply.warehouse'); }
function _ohbIso(d) { return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
function _ohbBtn(label, on, primary) {
  return `<button onclick="${on}" style="border:${primary ? '0' : '1px solid var(--border)'};border-radius:var(--r-sm);padding:${primary ? '7px 14px' : '3px 10px'};font-size:${primary ? 13 : 12}px;cursor:pointer;background:${primary ? 'var(--accent)' : 'transparent'};color:${primary ? 'var(--on-accent)' : 'var(--text-primary)'}">${label}</button>`;
}

// ── วิธีใช้หน้านี้ (PR-M2): the explanation of each pane, folded at its bottom and written for the person who reads it ──
// One block per pane; the warehouse is told what the warehouse does, ประสานงานขาย what it does (a role without its own
// text gets the shared one under `_`). Nothing here sits above a control.
const _OHB_ACC_GUIDE = '<a href="/docs/pilot/accounting_guide.html" target="_blank" style="color:var(--accent)">คู่มือบัญชี</a>';
const _OHB_HELP = {
  queue: {
    warehouse: ['ระบุยอดส่งจริงของแต่ละรายการภายใน 14:00 ของวันตัดรอบ แล้วกด “ยืนยันยอดส่ง” · กดที่เลขที่ใบเพื่อเปิดรายการ',
      'ยอดที่ต่างจากที่สาขายืนยันต้องเลือกเหตุผลของรายการนั้น · เลขที่ PO ไม่บังคับ แต่ควรกรอกไว้ใช้ติดตาม',
      'ป้าย “ส่งล่าช้า (มีค่าปรับ)” คือใบที่สาขาส่งหลัง 13:15 สองวันก่อนวันส่ง'],
    salessupport: ['ใบสั่งซื้อที่คลังจัดส่งแล้ว: ดาวน์โหลด Excel เพื่อคีย์เข้าระบบบัญชี แล้วติ๊กใบที่คีย์แล้วและกด “คีย์บัญชีแล้ว”',
      '“สรุปรวมตามวันส่ง” และไฟล์ Excel ใช้สถานะที่เลือกด้านบน · ป้าย “ส่งล่าช้า (มีค่าปรับ)” คือใบที่สาขาส่งหลัง 13:15',
      'มูลค่าของใบที่รอคลังคือ “มูลค่าที่สั่ง” เมื่อคลังจัดส่งแล้วเป็น “มูลค่าที่จัดส่ง”'],
    _: ['คลังระบุยอดส่งจริงภายใน 14:00 ของวันตัดรอบ แล้วกด “ยืนยันยอดส่ง” · ยอดที่ต่างจากที่สาขายืนยันต้องมีเหตุผล',
      'ประสานงานขายดาวน์โหลด Excel ของใบที่คลังจัดส่งแล้ว เพื่อคีย์เข้าระบบบัญชี · มูลค่าที่สั่ง = ยอดที่สาขายืนยัน, มูลค่าที่จัดส่ง = ยอดที่คลังส่งจริง'] },
  claims: {
    _: ['ตรวจรูปที่สาขาส่งทางไลน์ แล้วอนุมัติหรือไม่อนุมัติ · การไม่อนุมัติต้องระบุเหตุผลให้สาขา',
      'แจ้งขณะรับสินค้า: อนุมัติ = รับเข้าตามจริงอยู่แล้ว, ไม่อนุมัติ = นับส่วนที่เคลมเป็นรับเข้า',
      'แจ้งภายหลัง: อนุมัติ = หักออกจากรับเข้า, ไม่อนุมัติ = นับเป็นรับเข้า'] },
  issues: {
    operation: ['อ่านอย่างเดียว: ดูว่าแต่ละสาขายืนยันใบตัดเบิกตรงวัน ล่าช้า ยังไม่ยืนยัน หรือไม่มีใบ',
      'ใบที่ยืนยันหลังวันที่ของใบเป็น “ล่าช้า” · Excel รวมเฉพาะใบที่ยืนยันแล้ว'],
    _: ['ใบที่ยืนยันแล้วแก้ไขไม่ได้ · ประสานงานขายเปิดแก้ไขพร้อมเหตุผล แล้วสาขายืนยันใหม่',
      'ใบที่ยืนยันหลังวันที่ของใบเป็น “ล่าช้า” · Excel รวมเฉพาะใบที่ยืนยันแล้ว', _OHB_ACC_GUIDE] },
  issuevar: {
    _: ['เทียบมูลค่าที่เบิกจริงกับมูลค่าตามสูตรของแต่ละวัน · เลือกสาขาและเดือนก่อน',
      'วัตถุดิบกลุ่มมูลค่าสูงที่เบิกเกินสูตรมากกว่า 20% ติดต่อกัน 3 วันขายขึ้นไป แสดงเป็นเตือน · กดที่วันเพื่อดูรายการ'] },
  persales: {
    _: ['เทียบปริมาณวัตถุดิบที่ใช้กับยอดขาย (ไม่รวมภาษีมูลค่าเพิ่ม) ใช้ปริมาณเดียวกับที่ใช้คำนวณยอดแนะนำ เป็นอีกวิธีหนึ่งที่ใช้ดูควบคู่กัน ไม่ได้ใช้แทนการคำนวณตามเมนู',
      'แต่ละสาขาใช้ไม่เท่ากัน จึงดูเป็นรายสาขา · แกว่งต่อสัปดาห์ = ความผันผวนของอัตรารายสัปดาห์ ถ้าเกิน 15% แสดงว่าใช้ไม่สม่ำเสมอ ไม่ควรใช้ตัวเลขนี้เป็นเกณฑ์',
      'ช่วงก่อนหน้าไม่นับย้อนก่อนเปลี่ยนเป็นเมนูใหม่ (1 กันยายน 2026) · แถวทั้งเครือคือปริมาณรวมทุกสาขาหารด้วยยอดขายรวมทุกสาขา'] },
  comp: {
    _: ['แต่ละแถวคือหนึ่งรอบสั่งของสาขา เทียบ 4 ค่า (฿): ยอดแนะนำ · สาขายืนยัน · ส่งจริง · รับจริง',
      'ป้ายสถานะบอกว่าส่วนต่างอยู่ที่สาขา (สั่งเกิน หรือสั่งขาด) ที่คลัง หรือในการจัดส่ง · “ไม่ได้สั่ง” คือรอบที่ตัดรอบไปแล้วโดยสาขาไม่ส่งใบสั่งซื้อ'] },
  pilot: {
    _: ['สาขานำร่อง 4 สาขา 4 สัปดาห์ (อุบลราชธานี · อุดรธานี · ขอนแก่น · ระยอง)',
      'เกณฑ์ผ่าน: ใบตัดเบิกครบทุกวัน ≥ 95% · ยืนยันภายในวัน ≥ 90% · ผลต่างมีเหตุผล ≥ 80% · ความคลาดเคลื่อนพยากรณ์กลุ่มมูลค่าสูง ≤ 15%',
      'คู่มือ: <a href="/docs/pilot/branch_guide.html" target="_blank" style="color:var(--accent)">สาขา (1 หน้า)</a> · <a href="/docs/pilot/salessupport_guide.html" target="_blank" style="color:var(--accent)">ประสานงานขาย</a> · <a href="/docs/pilot/accounting_guide.html" target="_blank" style="color:var(--accent)">บัญชี</a> · <a href="/docs/pilot/pilot_plan.html" target="_blank" style="color:var(--accent)">แผนนำร่องและสคริปต์วิดีโอ</a>'] },
  central: {
    _: ['ขั้นที่ 2 นำเข้ารายงานของไดกิโตะ กรุ๊ปครั้งเดียว ได้ทั้งบัญชีสินค้าที่สั่งจากไดกิโตะ กรุ๊ป (หน่วย ราคา และรายการนอกบัญชีเป็น “สาขาซื้อเอง”) และใบรับสินค้าประจำเดือนของแต่ละสาขา · นำเข้าเดือนเดิมซ้ำจะแทนที่ใบเดิม · สาขาที่บันทึกรับเองครบแล้วไม่ต้องเลือก',
      'ขั้นที่ 3 จับคู่รหัสเดิมในสูตรกับรหัสของไดกิโตะ กรุ๊ป · มีผลต่อยอดแนะนำและผลต่าง ไม่มีผลต่อต้นทุน',
      'ขั้นที่ 4 นับก่อนเปิดร้านวันที่ 1 · นำเข้าต้นงวดก่อนปลายงวด',
      'ขั้นที่ 5 สาขายืนยันรับสินค้าครบและระบุเหตุผลผลต่าง (หรือประสานงานขายและฝ่ายวิเคราะห์ทำแทน)',
      'ขั้นที่ 6 บัญชีดาวน์โหลดใบสรุปตัดเบิก แล้วทำเครื่องหมาย “บันทึกเข้าระบบบัญชีแล้ว”'] },
  month: {
    _: ['รวมงวดที่ปิดในเดือนนี้ · ใช้จริง = ต้นงวด + รับเข้า − ปลายงวด เทียบกับยอดตามสูตร',
      'ข้อมูลส่วนนี้แสดงในรายงานผู้บริหาร (รายเดือน) ด้วย · กลุ่มมูลค่าสูง / กลาง / ต่ำ คือกลุ่มวัตถุดิบตามมูลค่า'] },
};
function _ohbRole() { return (window.AUTH && window.AUTH.enabled && window.AUTH.role) || 'management'; }
function ohbRenderHelp(pane) {
  const el = document.getElementById('ohbHelp-' + pane);
  if (!el) return;
  const role = _ohbRole();
  if (el.dataset.role === role && el.firstChild) return;     // already there (a re-render would close it)
  const h = _OHB_HELP[pane] || {};
  const lines = h[role] || h._ || [];
  el.dataset.role = role;
  el.innerHTML = lines.length ? bmcHelpHtml(lines.map(l => `<p>${l}</p>`).join('')) : '';
}

function loadOrderHub() {
  const t = new Date();
  const set = (id, v) => { const el = document.getElementById(id); if (el && !el.value) el.value = v; };
  set('ohbFrom', _ohbIso(new Date(t.getTime() - 7 * 864e5)));
  set('ohbTo', _ohbIso(new Date(t.getTime() + 7 * 864e5)));
  set('ohbCFrom', _ohbIso(new Date(t.getTime() - 13 * 864e5)));
  set('ohbCTo', _ohbIso(t));
  document.querySelectorAll('.ohb-key-only').forEach(el => { el.style.display = _ohbCanKey() ? '' : 'none'; });
  const centre = document.getElementById('ohbQCentre');           // the warehouse does not see the range, the consolidation or the Excel
  if (centre) centre.style.display = _ohbIsWarehouse() ? 'none' : '';
  if (_ohbIsWarehouse() && !_ohbStList().some(([k]) => k === _ohbStatus)) _ohbStatus = 'submitted';
  ohbSetSub(_ohbSub);
}
// ── the tab's ONE month (PR-M5) ──
const _OHB_MONTH_PANES = ['issues', 'issuevar', 'central', 'month'];
function _ohbMonthPrev(k) { return k === 'central' || k === 'month'; }       // the panes that close a month start at the previous one
function _ohbMonthSync(k) {
  const el = document.getElementById('ohbMonth'), bar = document.getElementById('ohbMonthBar');
  if (!el) return;
  supplyMonthPick(el, _ohbMonthPrev(k));
  if (bar) bar.style.display = _OHB_MONTH_PANES.includes(k) ? '' : 'none';
}
function _ohbMonthVal() {
  const el = document.getElementById('ohbMonth');
  if (!el) return '';
  if (!el.value) supplyMonthPick(el, _ohbMonthPrev(_ohbSub));
  return el.value;
}
function ohbMonthChanged() {
  const m = _ohbMonthVal();
  supplyCtxSet({ month: m });
  if (_ohbSub === 'issues') {             // the slip list follows the month: from its first day to its last (today in the running month)
    const last = _ohbIso(new Date(Number(m.slice(0, 4)), Number(m.slice(5, 7)), 0)), today = _ohbIso(new Date());
    document.getElementById('ohbIsFrom').value = m + '-01';
    document.getElementById('ohbIsTo').value = last > today && m === today.slice(0, 7) ? today : last;
  }
  ohbSetSub(_ohbSub);
}
function ohbSetSub(k) {
  _ohbSub = k;
  ['queue', 'comp', 'month', 'pilot', 'central', 'claims', 'issuevar', 'issues', 'persales'].forEach(p => { const el = document.getElementById('ohb-pane-' + p); if (el) el.style.display = p === k ? '' : 'none'; });
  _ohbMonthSync(k);
  ohbRenderHelp(k);
  if (k === 'issues') ohbLoadIssues();
  if (k === 'issuevar') ohbLoadIssueVar();
  if (k === 'persales') ohbLoadPerSales();
  if (k === 'queue') ohbLoadQueue();
  if (k === 'comp') ohbLoadComp();
  if (k === 'month') {
    const lk = document.getElementById('ohbMonthIssueLink'), hit = _supplyItemOf('bomcenter', 'issue');   // the link only for a role whose bar lists that page
    if (lk) lk.style.display = hit && _supplyItemAllowed(hit.item) ? '' : 'none';
    ohbLoadMonth();
  }
  if (k === 'pilot') ohbLoadPilot();
  if (k === 'central') ohbLoadCentral();
  if (k === 'claims') ohbLoadClaims();
  try { _updateClusterBar(activeTab); } catch (e) { /* no bar on this page */ }
}

// ── ปิดงวดเดือน (S11, reworked 2026-10-07): one month's close as numbered steps ──
// The statuses come from /api/bom-center/todo (close.steps); the centre's report is
// uploaded ONCE (the list + the month's goods-received notes, one confirm); the count
// import is pre-set to the branch and the date the checklist says is missing.
let _ohbClose = null;          // the /todo answer for the stepper's month
let _ohbMonthFile = null;      // the report picked in step 2
let _ohbMonthPre = null;       // its preview
const _OHB_STEP_ST = { done: ['เสร็จแล้ว', 'var(--good-ink)'], todo: ['รอทำ', 'var(--amber-ink)'],
                       blocked: ['รอขั้นตอนก่อนหน้า', 'var(--text-muted)'], na: ['ไม่มีงาน', 'var(--text-muted)'] };

function _ohbCloseMonth() { return _ohbMonthVal(); }
function _ohbStChip(s) { const m = _OHB_STEP_ST[s] || _OHB_STEP_ST.na; return _bmcChip(m[0], m[1]); }
function _ohbBranchLines(step, linkFn) {
  const ink = s => s === 'done' ? 'var(--good-ink)' : s === 'todo' ? 'var(--amber-ink)' : 'var(--text-muted)';
  return (step.branches || []).map(b => `<div style="color:${ink(b.status)}">${b.status === 'done' ? '✓' : '•'} ${esc(b.branch)}: ${esc(b.detail)}${linkFn && b.status !== 'done' ? ' ' + linkFn(b) : ''}</div>`).join('')
    || `<div style="color:var(--text-muted)">${esc(step.detail || '')}</div>`;
}
async function ohbLoadCentral() {
  const month = _ohbCloseMonth();
  const mlbl = document.getElementById('ohbCloseMonthLbl'); if (mlbl) mlbl.textContent = fmtMonthTh(month);
  _bmcStatus('ohbCloseStatus', 'กำลังโหลด…');
  try {
    const [d, t] = await Promise.all([_bmcJson(`${API}/api/bom-center/central`),
                                      _bmcJson(`${API}/api/bom-center/todo?month=${month}`)]);
    _ohbClose = t;
    const st = {}; (t.close.steps || []).forEach(s => { st[s.key] = s; });
    document.getElementById('ohbCloseSummary').textContent = `เสร็จ ${t.close.done} จาก ${t.close.total} ขั้นตอน · สาขาที่เริ่มปิดงวด: ${(t.close.branches || []).map(b => b.name).join(' · ') || 'ยังไม่มี'}`;
    const set = (n, status) => { const el = document.getElementById('ohbSt' + n); if (el) el.innerHTML = status ? _ohbStChip(status) : ''; };
    set(1, st.release_in_force && st.release_in_force.status);
    document.getElementById('ohbSt1Detail').textContent = st.release_in_force ? st.release_in_force.detail : '';
    // step 2 = the list and the month's notes together
    set(2, st.central_report && st.deliveries ? (st.central_report.status === 'done' && st.deliveries.status === 'done' ? 'done' : 'todo') : 'todo');
    document.getElementById('ohbCenStat').innerHTML = (d.count
      ? `บัญชีสินค้าส่วนกลาง <b>${d.count}</b> รายการ · สาขาซื้อเอง ${d.local} รายการ${d.needs_factor ? ` · ${_bmcChip(d.needs_factor + ' รายการยังไม่ระบุอัตราแปลงหน่วย', 'var(--amber-ink)')}` : ''}`
      : _bmcChip('ยังไม่มีบัญชีสินค้าส่วนกลาง (ยอดแนะนำถือว่าสั่งจากไดกิโตะ กรุ๊ปทุกรายการ)', 'var(--amber-ink)'))
      + (st.deliveries ? `<div class="mt-1">ใบรับสินค้าประจำเดือน${fmtMonthTh(month)}:${_ohbBranchLines(st.deliveries)}</div>` : '');
    set(3, st.code_links && st.code_links.status);
    ohbLoadLinks();
    // step 4 = the two counts
    const c4 = [st.begin_count, st.end_count].filter(Boolean);
    set(4, c4.length ? (c4.every(s => s.status === 'done') ? 'done' : c4.some(s => s.status === 'todo') ? 'todo' : 'blocked') : null);
    document.getElementById('ohbSt4Detail').innerHTML = c4.map(s => `<div class="mt-1"><b>${esc(s.title)}</b> ${_ohbStChip(s.status)}${_ohbBranchLines(s)}</div>`).join('');
    set(5, st.receipts_confirm && st.receipts_confirm.status);
    document.getElementById('ohbSt5Detail').innerHTML = st.receipts_confirm ? _ohbBranchLines(st.receipts_confirm,
      b => `<a href="#" onclick="supplyGo({tab:'branchstock',pane:'var',branch:${b.branch_id}});return false" style="color:var(--accent);text-decoration:underline">เปิดผลต่างของสาขา →</a>`) : '';
    set(6, st.issue_posted && st.issue_posted.status);
    const issueOk = (hit => !!hit && _supplyItemAllowed(hit.item))(_supplyItemOf('bomcenter', 'issue'));   // the link only for a role whose bar lists that page
    const lk6 = document.getElementById('ohbSt6Link'); if (lk6) lk6.style.display = issueOk ? '' : 'none';
    document.getElementById('ohbSt6Detail').innerHTML = st.issue_posted ? _ohbBranchLines(st.issue_posted,
      issueOk ? b => `<a href="#" onclick="supplyGo({tab:'bomcenter',pane:'issue',branch:${b.branch_id},month:'${esc(month)}'});return false" style="color:var(--accent);text-decoration:underline">เปิดใบสรุปตัดเบิกของสาขา →</a>` : null) : '';
    set(7, st.monthly_summary && st.monthly_summary.status);
    // the count import: the branch from the shared context, the date the checklist says is missing
    const sel = document.getElementById('ohbCntBranch');
    if (!sel) return;                       // a pinned login never reaches this centre page, and has no picker
    if (!sel.options.length) {
      const me = await _bmcJson(`${API}/api/bom-center/stock/me`);
      sel.innerHTML = me.branches.map(b => `<option value="${b.branch_id}">${esc(b.name)}</option>`).join('');
      const ctx = supplyCtx().branch_id;
      if (ctx && me.branches.some(b => b.branch_id === ctx)) sel.value = String(ctx);
    }
    // unless the chosen branch is one the checklist still waits a count for, take the first that is
    const pending = c4.flatMap(s => (s.branches || []).filter(b => b.status !== 'done').map(b => String(b.branch_id)));
    if (pending.length && !pending.includes(sel.value) && [...sel.options].some(o => o.value === pending[0])) sel.value = pending[0];
    ohbCntBranchChanged(true);
    _bmcStatus('ohbCloseStatus', '');
  } catch (e) { _bmcStatus('ohbCloseStatus', String(e.message || e), 'var(--bad-ink)'); }
}
function ohbCntBranchChanged(quiet) {
  const sel = document.getElementById('ohbCntBranch'), day = document.getElementById('ohbCntDate');
  const bid = Number(sel.value);
  if (!quiet) supplyCtxSet({ branch_id: bid || null });
  if (!_ohbClose || !bid) return;
  const st = {}; (_ohbClose.close.steps || []).forEach(s => { st[s.key] = s; });
  const pend = [st.begin_count, st.end_count].filter(Boolean)
    .map(s => (s.branches || []).find(b => b.branch_id === bid)).find(b => b && b.status !== 'done');
  const want = pend && pend.go && pend.go.extra ? pend.go.extra.count_date : '';
  if (want) day.value = want;
}
async function ohbLoadLinks() {
  const box = document.getElementById('ohbLinks');
  try {
    const d = await _bmcJson(`${API}/api/bom-center/central/links`);
    const kept = d.links.map(l => `<div class="text-xs" style="border-top:1px solid var(--border);padding:4px 0;color:var(--text-primary)">
        ${esc(l.old_code)} ${esc(l.old_name || '')} → <b>${esc(l.new_code)}</b> ${esc(l.new_name || '')}
        <span style="color:var(--text-muted)">· ${esc(whoTh(l.who) === '-' ? '' : whoTh(l.who))}</span>
        <button onclick="ohbLinkSave([{old_code:${_bmcQ(l.old_code)},new_code:null}])" style="font-size:11px;color:var(--bad-ink);background:none;border:0;cursor:pointer">ยกเลิกการจับคู่</button></div>`).join('');
    const sugg = d.suggestions.map(s => `<div class="text-xs" style="border-top:1px solid var(--border);padding:5px 0;color:var(--text-primary)">
        รหัสในสูตร <b>${esc(s.old_code)}</b> ${esc(s.old_name || '')} (ไม่อยู่ในบัญชีสินค้าส่วนกลาง) · รหัสในบัญชีที่ใกล้เคียง:
        ${s.options.map(o => `<button onclick="ohbLinkSave([{old_code:${_bmcQ(s.old_code)},new_code:${_bmcQ(o.new_code)}}])" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:2px 8px;margin:2px;font-size:11px;cursor:pointer;background:transparent;color:var(--text-primary)">
          ${esc(o.new_code)} ${esc((o.new_name || '').slice(0, 40))} <span style="color:var(--text-muted)">(${o.score})</span> · จับคู่</button>`).join('')}</div>`).join('');
    box.innerHTML = (d.links.length ? `<div class="text-xs font-semibold" style="color:var(--text-secondary)">จับคู่แล้ว ${d.links.length} รายการ</div>${kept}` : '') +
      (d.suggestions.length ? `<div class="text-xs font-semibold mt-2" style="color:var(--amber-ink)">คู่ที่แนะนำ ${d.suggestions.length} คู่</div>${sugg}` : '') +
      (d.orphans.length ? `<div class="text-[11px] mt-2" style="color:var(--text-muted)">วัตถุดิบในบัญชีสินค้าส่วนกลางที่ไม่มีสูตรใช้และไม่พบรหัสคู่ ${d.orphans.length} รายการ: ${d.orphans.slice(0, 20).map(o => esc(o.code)).join(', ')}${d.orphans.length > 20 ? ' …' : ''}</div>` : '') ||
      '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีบัญชีสินค้าส่วนกลาง</span>';
  } catch (e) { box.textContent = String(e.message || e); }
}
async function ohbLinkSave(rows) {
  try {
    await _bmcJson(`${API}/api/bom-center/central/links`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(rows) });
    _bmcStatus('ohbLinkStatus', '✓ บันทึกแล้ว', 'var(--good-ink)');
    ohbLoadLinks();
  } catch (e) { _bmcStatus('ohbLinkStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
function _ohbFd(file) { const fd = new FormData(); fd.append('file', file, file.name); return fd; }
function _ohbCenRow(r) {
  const flag = r.status === 'skip' ? _bmcChip('ไม่นำเข้า', 'var(--text-muted)')
    : r.status === 'needs_factor' ? _bmcChip('ต้องระบุอัตราแปลง', 'var(--bad-ink)')
    : r.pack_change ? _bmcChip('ขนาดบรรจุต่างจากทะเบียน', 'var(--amber-ink)')
    : (r.factor_how || '').startsWith('จากทะเบียน') ? _bmcChip('ควรตรวจ', 'var(--amber-ink)')
    : r.is_new ? _bmcChip('รหัสใหม่', 'var(--navy-ink)') : '';
  return `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(r.code)}</td><td class="pr-2">${esc(r.name)}</td>
    <td class="pr-2">${esc(r.unit_label || '')}</td><td class="pr-2 text-right">${r.price_per_sale != null ? _bmcNum(r.price_per_sale, 0) : '–'}</td>
    <td class="pr-2">1 ${esc(r.sale_unit || '')} = ${r.base_per_sale != null ? _bmcNum(r.base_per_sale, 0) + ' ' + esc(r.base_unit || '') : '?'}
      ${r.old_per && r.base_per_sale && r.pack_change ? `<span style="color:var(--text-muted)">(ทะเบียน ${_bmcNum(r.old_per, 0)})</span>` : ''}</td>
    <td class="pr-2" style="color:var(--text-muted)">${esc(r.factor_how || '')}</td><td>${flag}</td></tr>`;
}
// Step 2: the report once. One preview shows the list's changes AND the notes per branch;
// the checkboxes only choose which branches the one save books (no re-upload per toggle).
function ohbMonthPick(input) {
  const file = input?.files?.[0];
  if (!file) return;
  _ohbMonthFile = file;
  input.value = '';
  bmcUcReset('ohbUcCentral');                    // PR-Y: a new file starts with no answers
  ohbMonthPreview();
}
async function ohbMonthPreview() {
  if (!_ohbMonthFile) return;
  const setLocal = document.getElementById('ohbSetLocal').checked;
  try {
    _bmcStatus('ohbCenStatus', 'กำลังอ่านไฟล์…');
    const pre = await _bmcJson(`${API}/api/bom-center/central/month-import?dry_run=true&set_local=${setLocal}`, { method: 'POST', body: _ohbFd(_ohbMonthFile) });
    _ohbMonthPre = pre;
    const c = pre.catalogue;
    // PR-Y: a code the register does not know may be a new code of an item it has - the person answers per code before anything is added
    const fresh = c.rows.filter(r => r.is_new && r.status !== 'skip').map(r => ({ code: r.code, name: r.name, unit: r.unit_label, suggestions: r.suggestions || [] }));
    await bmcUcRender('ohbUcCentral', fresh, { mode: 'central', needAll: true, prefillNew: true });
    const look = c.rows.filter(r => r.status !== 'ok' || r.pack_change || r.is_new || (r.factor_how || '').startsWith('จากทะเบียน'));
    const monthWarn = pre.month && pre.month !== _ohbCloseMonth() ? ' ' + _bmcChip(`งวดในไฟล์ ${fmtMonthTh(pre.month)} ต่างจากงวดที่เลือก ${fmtMonthTh(_ohbCloseMonth())}`, 'var(--amber-ink)') : '';
    document.getElementById('ohbCenReport').innerHTML = `<div class="text-xs font-semibold" style="color:var(--text-primary)">บัญชีสินค้าส่วนกลาง${monthWarn}</div>
      <div class="text-xs" style="color:var(--text-secondary)">${esc(pre.sheet)} (${fmtMonthTh(pre.month || '?')}) · <b>${c.items}</b> รายการ · รหัสใหม่ ${c.new} · ขนาดบรรจุต่างจากทะเบียน ${c.pack_change} ·
      ใช้ขนาดบรรจุตามทะเบียน (ควรตรวจ) ${c.from_master} · ยังไม่ระบุอัตราแปลง ${c.needs_factor} · ไม่มีราคา ${c.no_price} · ไม่นำเข้า ${c.skipped} ·
      <b>รหัสอื่นอีก ${c.to_local} รายการจะกำหนดเป็น “สาขาซื้อเอง”</b></div>
      ${look.length ? `<div class="overflow-x-auto mt-2" style="max-height:300px;overflow-y:auto"><table class="w-full text-xs" style="color:var(--text-primary)">
      <thead><tr style="color:var(--text-muted)" class="text-left"><th>รหัส</th><th>ชื่อวัตถุดิบ</th><th>หน่วยนับ</th><th class="text-right">ราคา</th><th>อัตราแปลงหน่วย</th><th>ที่มา</th><th></th></tr></thead>
      <tbody>${look.map(_ohbCenRow).join('')}</tbody></table></div>
      <div class="text-[11px] mt-1" style="color:var(--text-muted)">แสดงเฉพาะ ${look.length} รายการที่ควรตรวจ · อัตราแปลงผิด: ดาวน์โหลดบัญชีสินค้า แก้ “1 หน่วยขาย = หน่วยฐาน” แล้วนำเข้าใหม่</div>` : ''}`;
    const dl = pre.deliveries;
    if (!dl.branches.length) {              // a file without a month or quantities (the list's own export)
      document.getElementById('ohbDelReport').innerHTML = `<div class="text-xs mt-2" style="color:var(--text-muted)">${esc(dl.note || 'ไฟล์นี้ไม่มียอดจัดส่งรายสาขา จึงไม่สร้างใบรับสินค้า')}</div>
        <button onclick="ohbMonthSave()" class="mt-2" style="border:0;border-radius:var(--r-sm);padding:6px 14px;font-size:13px;cursor:pointer;background:var(--accent);color:var(--on-accent)">บันทึกบัญชีสินค้า</button>`;
      _bmcStatus('ohbCenStatus', 'ดูตัวอย่างด้านล่าง แล้วกดบันทึก');
      return;
    }
    document.getElementById('ohbDelReport').innerHTML = `<div class="text-xs font-semibold mt-2" style="color:var(--text-primary)">ใบรับสินค้าประจำเดือน${fmtMonthTh(dl.month)} · ลงวันที่ ${esc(fmtDay(dl.receipt_date))} · เลือกสาขาที่จะบันทึก</div>
      ${dl.branches.map(b => `<label class="flex flex-wrap items-center gap-2 text-xs mt-1" style="color:var(--text-primary)">
        <input type="checkbox" data-bid="${b.branch_id || ''}" ${b.branch_id && !b.error && b.lines ? 'checked' : ''} ${b.branch_id && !b.error && b.lines ? '' : 'disabled'}>
        <b>${esc(b.code)} ${esc(b.branch || b.label)}</b> ${b.error ? _bmcChip(b.error, 'var(--bad-ink)') : ''}
        ${b.branch_id ? `· ${b.lines} รายการ · ฿${_bmcNum(b.value_central, 0)} (ราคาขายของไดกิโตะ กรุ๊ป)` : ''}
        ${b.replaced ? _bmcChip('แทนที่ใบรับเดิม ' + b.replaced + ' ใบ', 'var(--amber-ink)') : ''}
        ${b.skipped && b.skipped.length ? _bmcChip(b.skipped.length + ' รายการแปลงหน่วยไม่ได้ (ไม่บันทึก)', 'var(--amber-ink)') : ''}</label>`).join('')}
      <div class="flex flex-wrap items-center gap-2 mt-2">
        <button onclick="ohbMonthSave()" style="border:0;border-radius:var(--r-sm);padding:6px 14px;font-size:13px;cursor:pointer;background:var(--accent);color:var(--on-accent)">บันทึกบัญชีสินค้าและใบรับสินค้าที่เลือก</button>
        <span class="text-[11px]" style="color:var(--text-muted)">ไม่เลือกสาขาใด = บันทึกเฉพาะบัญชีสินค้า</span></div>`;
    _bmcStatus('ohbCenStatus', 'ดูตัวอย่างด้านล่าง แล้วกดบันทึก');
  } catch (e) { _bmcStatus('ohbCenStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
async function ohbMonthSave() {
  if (!_ohbMonthFile || !_ohbMonthPre) return;
  const open = bmcUcOpen('ohbUcCentral');
  if (open.length) {
    _bmcStatus('ohbCenStatus', `ยังไม่ได้เลือก ${open.length} รหัสในรายการ “รหัสที่ระบบไม่รู้จัก”: ${open.slice(0, 4).join(', ')}${open.length > 4 ? ' …' : ''}`, 'var(--amber-ink)');
    const box = document.getElementById('ohbUcCentral'); if (box) box.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }
  const choices = bmcUcChoices('ohbUcCentral'), cs = bmcUcSummary('ohbUcCentral');
  const ids = [...document.querySelectorAll('#ohbDelReport input[data-bid]')].filter(x => x.checked).map(x => x.dataset.bid);
  const c = _ohbMonthPre.catalogue;
  const names = ids.map(id => (_ohbMonthPre.deliveries.branches.find(b => String(b.branch_id) === id) || {}).branch).filter(Boolean);
  if (!await uiConfirm(`บันทึกบัญชีสินค้าส่วนกลาง ${c.items} รายการ (วัตถุดิบใหม่ ${c.new - cs.alias} · รหัสอื่นอีก ${c.to_local} รายการเป็น “สาขาซื้อเอง” · หน่วยสั่งในทะเบียนเปลี่ยนเป็นหน่วยขายของไดกิโตะ กรุ๊ป)`
      + (cs.alias ? `\n${cs.alias} รหัสเป็นรหัสใหม่ของวัตถุดิบเดิม ${_bmcCanPrices() ? 'บันทึกเป็นรหัสในระบบอื่นทันที' : 'ส่งเป็นคำขอให้ฝ่ายวิเคราะห์ และยังไม่นำเข้าในครั้งนี้'}` : '')
      + (ids.length ? `\nและใบรับสินค้าประจำเดือน${fmtMonthTh(_ohbMonthPre.month)} ของ ${names.join(', ')} (ใบเดิมของเดือนนี้ถูกแทนที่)` : '\nไม่บันทึกใบรับสินค้า'),
      { title: 'ยืนยันการบันทึกบัญชีสินค้า', ok: 'บันทึก', cancel: 'ยกเลิก' })) { _bmcStatus('ohbCenStatus', 'ยกเลิกแล้ว ยังไม่บันทึก'); return; }
  try {
    const setLocal = document.getElementById('ohbSetLocal').checked;
    const fd = _ohbFd(_ohbMonthFile);
    fd.append('code_choices', JSON.stringify(choices)); fd.append('alias_system', bmcUcSystem('ohbUcCentral'));
    const d = await _bmcJson(`${API}/api/bom-center/central/month-import?dry_run=false&set_local=${setLocal}${ids.length ? '&branch_ids=' + ids.join(',') : ''}`, { method: 'POST', body: fd });
    const dl = d.deliveries.branches.filter(b => ids.includes(String(b.branch_id)));
    _bmcStatus('ohbCenStatus', `✓ บัญชีสินค้า ${d.catalogue.saved} รายการ (วัตถุดิบใหม่ ${d.catalogue.inserted}${d.catalogue.aliased ? ` · รหัสใหม่ของวัตถุดิบเดิม ${d.catalogue.aliased}` : ''}${d.catalogue.requested ? ` · ส่งคำขอให้ฝ่ายวิเคราะห์ ${d.catalogue.requested}` : ''}) · สาขาซื้อเอง ${d.catalogue.set_local} รายการ`
      + (dl.length ? ' · ใบรับ: ' + dl.map(b => `${b.branch || b.label} ${b.receipt_id ? 'เลขที่ ' + b.receipt_id + ' (' + b.lines + ' รายการ)' : (b.error || 'ไม่มีรายการ')}`).join(' · ') : ''), 'var(--good-ink)');
    _ohbMonthFile = null; _ohbMonthPre = null;
    bmcUcReset('ohbUcCentral');
    document.getElementById('ohbDelReport').innerHTML = '';
    ohbLoadCentral();
  } catch (e) { _bmcStatus('ohbCenStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
// Step 4: a count from the template, dated the day it was counted.
function _ohbCntQS() {
  return `branch_id=${document.getElementById('ohbCntBranch').value}&count_date=${document.getElementById('ohbCntDate').value}`;
}
function ohbCntTemplate() { bmcDownload(`${API}/api/bom-center/stock/count-template.xlsx?${_ohbCntQS()}`, 'แบบฟอร์มตรวจนับ.xlsx', 'ohbCntStatus'); }
function ohbCentralXlsx() { bmcDownload(`${API}/api/bom-center/central.xlsx`, 'บัญชีสินค้าส่วนกลาง.xlsx', 'ohbCenStatus'); }
async function ohbCntImport(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const day = document.getElementById('ohbCntDate').value;
  try {
    if (!day) throw new Error('เลือกวันที่ตรวจนับ');
    const pre = await _bmcJson(`${API}/api/bom-center/stock/count-import?dry_run=true&${_ohbCntQS()}`, { method: 'POST', body: _ohbFd(file) });
    document.getElementById('ohbCntReport').innerHTML = `<div class="text-xs" style="color:var(--text-secondary)">วันที่ตรวจนับ ${esc(fmtDay(pre.count_date))} · ${pre.lines} รายการ
      ${pre.unknown.length ? `<div style="color:var(--bad-ink)">ไม่พบรหัสในทะเบียน: ${pre.unknown.slice(0, 12).map(esc).join(', ')}</div>` : ''}
      ${pre.unconverted.length ? `<div style="color:var(--bad-ink)">แปลงหน่วยไม่ได้: ${pre.unconverted.slice(0, 8).map(esc).join(' · ')}</div>` : ''}</div>`;
    if (!pre.ok) { _bmcStatus('ohbCntStatus', 'แก้ไขไฟล์ก่อนนำเข้า', 'var(--bad-ink)'); return; }
    const name = document.getElementById('ohbCntBranch').selectedOptions[0]?.textContent || '';
    if (!await uiConfirm(`ส่งผลตรวจนับ สาขา${name} วันที่ ${fmtDay(day)} (${pre.lines} รายการ)\nส่งแล้วแก้ไม่ได้ ต้องการนับใหม่ให้นำเข้าเป็นวันที่ใหม่`, { title: 'ยืนยันการส่งผลตรวจนับ', ok: 'ส่งผลตรวจนับ', cancel: 'ยกเลิก' })) { _bmcStatus('ohbCntStatus', 'ยกเลิกแล้ว'); return; }
    const d = await _bmcJson(`${API}/api/bom-center/stock/count-import?dry_run=false&${_ohbCntQS()}`, { method: 'POST', body: _ohbFd(file) });
    _bmcStatus('ohbCntStatus', `✓ ส่งผลตรวจนับเลขที่ ${d.count_id} (${d.kind === 'begin' ? 'ต้นงวดแรก' : 'ปลายงวด'}) · ${d.lines} รายการ`, 'var(--good-ink)');
    ohbLoadCentral();                       // the next missing date, the step statuses
  } catch (e) { _bmcStatus('ohbCntStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
  finally { input.value = ''; }
}

// ── queue ────────────────────────────────────────────────────────────────────
function ohbSetStatus(s) { _ohbStatus = s; _ohbKeyedSel.clear(); ohbLoadQueue(); }
function _ohbRange() { return `date_from=${document.getElementById('ohbFrom').value}&date_to=${document.getElementById('ohbTo').value}`; }
// the consolidation and the workbook follow the status chip (open = pending + processed is the server's default)
// "ยังไม่คีย์บัญชี" exports exactly the processed / received orders listed (by id), the ones still to key
function _ohbExportQS() {
  if (_ohbStatus !== 'unkeyed') return `${_ohbRange()}&status=${_ohbStatus}`;
  const ids = ((_ohbQ && _ohbQ.orders) || []).map(o => o.order_id);
  return ids.length ? `ids=${ids.join(',')}&status=all` : `${_ohbRange()}&status=all`;
}
async function ohbLoadQueue() {
  _ohbWasPhone = _ohbPhone();
  document.getElementById('ohbStatusChips').innerHTML = _ohbStList().map(([k, t]) => {
    const on = k === _ohbStatus;
    return `<button onclick="ohbSetStatus('${k}')" class="ohb-chipbtn" style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:5px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">${t}</button>`;
  }).join('');
  const box = document.getElementById('ohbQueue');
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    const unkeyed = _ohbStatus === 'unkeyed';
    _ohbQ = await _bmcJson(`${API}/api/bom-center/hub/queue?status=${unkeyed ? 'all&keyed=0' : _ohbStatus}&${_ohbRange()}`);
    if (unkeyed) _ohbQ.orders = _ohbQ.orders.filter(_ohbKeyable);          // only a processed / received order can be keyed
    ohbRenderQueueList();
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
// PR-M5: one order of the queue as a card on a phone — branch · delivery round · lines · value · chips. The whole card opens the order.
const _OHB_ST_CHIP = { submitted: ['รอยืนยันยอดส่ง', 'var(--amber-ink)'], processed: ['คลังจัดส่งแล้ว', 'var(--good-ink)'], received: ['สาขารับแล้ว', 'var(--good-ink)'],
  cancelled: ['ยกเลิก', 'var(--text-muted)'] };
function _ohbCard(o) {
  const st = _OHB_ST_CHIP[o.status] || [o.status_th || o.status, 'var(--text-muted)'];
  const wh = _ohbIsWarehouse();
  const chips = [_bmcChip(st[0] + (o.status === 'processed' && o.po_ref ? ' · ' + o.po_ref : ''), st[1]),
    o.late ? _bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)') : '',
    o.branch_changes ? _bmcChip(`สาขาแก้ไข ${o.branch_changes} รายการ`, 'var(--amber-ink)') : '',
    o.processed_on_time === false ? _bmcChip('ยืนยันยอดส่งหลัง 14:00', 'var(--bad-ink)') : '',
    wh ? '' : (o.accounting_keyed ? _bmcChip(`คีย์บัญชีแล้ว ${fmtDay(o.accounting_keyed_date || '')}`, 'var(--good-ink)') : (_ohbKeyable(o) ? _bmcChip('ยังไม่คีย์บัญชี', 'var(--amber-ink)') : '')),
    o.claims_pending ? _bmcChip('มีเคลม รอพิจารณา', 'var(--amber-ink)') : (o.claims && !wh ? _bmcChip('มีเคลม', 'var(--text-muted)') : '')].filter(Boolean).join(' ');
  return `<div class="ohb-card" role="button" tabindex="0" data-order="${o.order_id}" onclick="ohbOpen(${o.order_id})" onkeydown="if(event.key==='Enter'){ohbOpen(${o.order_id})}">
    ${_ohbCanKey() && _ohbKeyable(o) ? `<label class="ohb-card-chk" onclick="event.stopPropagation()"><input type="checkbox" aria-label="เลือกใบสั่งซื้อของ${esc(o.branch)}" ${_ohbKeyedSel.has(o.order_id) ? 'checked' : ''} onchange="ohbKeyToggle(${o.order_id},this.checked)"></label>` : ''}
    <div class="ohb-card-main">
      <div class="ohb-card-t">${esc(o.branch)}</div>
      <div class="ohb-card-s">${esc(_OHB_RND[o.round] || o.round)} · ${o.lines} รายการ</div>
      <div class="ohb-card-v">${esc(o.value_label || 'มูลค่า')} ฿${_bmcNum(o.value, 0)}</div>
      <div class="ohb-card-c">${chips}</div></div></div>`;
}
// The list of the queue. It is drawn from _ohbQ and is re-drawn alone after the warehouse confirms an order: the order stays
// in the list with its new status (the list is filtered again only the next time it is loaded), so it never "vanishes".
// The value of an order is ONE number from the server (hub.with_values) with the words that say which: ordered or sent.
function ohbRenderQueueList() {
  const box = document.getElementById('ohbQueue');
  if (!box || !_ohbQ) return;
  {
    const shown = new Set(_ohbQ.orders.map(o => o.order_id));
    [..._ohbKeyedSel].forEach(i => { if (!shown.has(i)) _ohbKeyedSel.delete(i); });
    const byDay = {};
    _ohbQ.orders.forEach(o => { (byDay[o.delivery] = byDay[o.delivery] || []).push(o); });
    const days = Object.keys(byDay).sort();
    const sumBy = (rows, lbl) => rows.filter(o => o.value_label === lbl).reduce((a, o) => a + (o.value || 0), 0);
    const dayTotal = rows => [['มูลค่าที่สั่ง'], ['มูลค่าที่จัดส่ง']].map(([l]) => rows.some(o => o.value_label === l) ? `${l} ฿${_bmcNum(sumBy(rows, l), 0)}` : '').filter(Boolean).join(' · ');
    const phone = _ohbPhone();
    box.innerHTML = days.length ? days.map(d => `<div class="text-xs font-semibold mt-2 mb-1" style="color:var(--text-primary)">วันส่ง ${esc(fmtDay(d))} · ${byDay[d].length} ใบสั่งซื้อ · ${dayTotal(byDay[d])}</div>` +
      byDay[d].map(o => phone ? _ohbCard(o) : `<div style="border-top:1px solid var(--border);padding:6px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:baseline" class="text-xs">
        ${_ohbCanKey() && _ohbKeyable(o) ? `<input type="checkbox" aria-label="เลือกใบสั่งซื้อ #${o.order_id}" ${_ohbKeyedSel.has(o.order_id) ? 'checked' : ''} onchange="ohbKeyToggle(${o.order_id},this.checked)">` : ''}
        <a href="#" onclick="ohbOpen(${o.order_id});return false" style="color:var(--accent);font-weight:600">#${o.order_id} ${esc(o.branch)}</a>
        <span>${esc(_OHB_RND[o.round] || o.round)} · ตัดรอบ ${esc(fmtDay(o.cycle_date))}</span><span>${o.lines} รายการ · ${esc(o.value_label || 'มูลค่า')} ฿${_bmcNum(o.value, 0)}</span>
        ${o.branch_changes ? _bmcChip(`สาขาแก้ไข ${o.branch_changes} รายการ`, 'var(--amber-ink)') : ''}
        ${o.buffer_pct ? _bmcChip(`สาขาปรับ ${o.buffer_pct > 0 ? '+' : ''}${_bmcNum(o.buffer_pct, 0)}% (${esc(o.buffer_reason_th || '')})`, 'var(--navy-ink)') : ''}
        ${o.stock_deducted ? '' : _bmcChip('ไม่มีผลตรวจนับสิ้นเดือน', 'var(--text-muted)')}
        ${o.late ? _bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)') : ''}
        ${o.processed_on_time === false ? _bmcChip('ยืนยันยอดส่งหลัง 14:00', 'var(--bad-ink)') : ''}
        ${o.status === 'processed' ? _bmcChip(`คลังจัดส่งแล้ว${o.po_ref ? ' · ' + o.po_ref : ''}`, 'var(--good-ink)') : ''}
        ${o.status === 'received' ? _bmcChip('สาขารับแล้ว', 'var(--good-ink)') : ''}
        ${o.accounting_keyed ? _bmcChip(`คีย์บัญชีแล้ว ${esc(fmtDay(o.accounting_keyed_date || ''))}`, 'var(--good-ink)') : (_ohbKeyable(o) ? _bmcChip('ยังไม่คีย์บัญชี', 'var(--amber-ink)') : '')}
        ${o.claims_pending ? _bmcChip('มีเคลม รอพิจารณา', 'var(--amber-ink)') : (o.claims ? _bmcChip('มีเคลม', 'var(--text-muted)') : '')}</div>`).join('')).join('')
      : '<span class="text-xs" style="color:var(--good-ink)">✓ ไม่มีใบสั่งซื้อในช่วงนี้</span>';
    _ohbKeySync();
  }
}

// ── ทำเครื่องหมายใบที่คีย์เข้าระบบบัญชีแล้ว (ประสานงานขาย) ──
function _ohbKeySync() {
  const all = document.getElementById('ohbKeyAll');
  const keyable = ((_ohbQ && _ohbQ.orders) || []).filter(_ohbKeyable);
  if (all) all.checked = keyable.length > 0 && keyable.every(o => _ohbKeyedSel.has(o.order_id));
}
function ohbKeyToggle(id, on) { if (on) _ohbKeyedSel.add(id); else _ohbKeyedSel.delete(id); _ohbKeySync(); }
function ohbKeySelectAll(on) {
  ((_ohbQ && _ohbQ.orders) || []).filter(_ohbKeyable).forEach(o => { if (on) _ohbKeyedSel.add(o.order_id); else _ohbKeyedSel.delete(o.order_id); });
  document.querySelectorAll('#ohbQueue input[type=checkbox]').forEach(c => { c.checked = !!on; });
  _ohbKeySync();
}
async function ohbMarkKeyed(keyed) {
  const ids = [..._ohbKeyedSel];
  if (!ids.length) { _bmcStatus('ohbKeyStatus', 'เลือกใบสั่งซื้ออย่างน้อย 1 ใบ', 'var(--bad-ink)'); return; }
  if (!await uiConfirm(`ใบสั่งซื้อ ${ids.length} ใบ ${keyed ? 'คีย์เข้าระบบบัญชีแล้ว' : 'ยังไม่ได้คีย์เข้าระบบบัญชี'}?`, { title: keyed ? 'ยืนยันการคีย์บัญชี' : 'ยืนยันการยกเลิกคีย์บัญชี', ok: keyed ? 'คีย์บัญชีแล้ว' : 'ยกเลิกคีย์บัญชี', cancel: 'ยกเลิก' })) { _bmcStatus('ohbKeyStatus', 'ยกเลิกแล้ว'); return; }
  try {
    const r = await _bmcJson(`${API}/api/bom-center/hub/keyed`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order_ids: ids, keyed }) });
    _ohbKeyedSel.clear();
    await ohbLoadQueue();
    _bmcStatus('ohbKeyStatus', `✓ ${keyed ? 'คีย์บัญชีแล้ว' : 'ยกเลิกคีย์บัญชี'} ${r.updated} ใบ`, 'var(--good-ink)');
    if (_ohbOrder && ids.includes(_ohbOrder.order_id)) ohbOpen(_ohbOrder.order_id);
  } catch (e) { _bmcStatus('ohbKeyStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}

async function ohbOpen(id) {
  const box = document.getElementById('ohbDetail');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    _ohbOrder = await _bmcJson(`${API}/api/bom-center/hub/order/${id}`);
    ohbRenderDetail();
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
// 1.1: the unit of an order line as the API words it (`unit_word`, `unit_label` of orders.detail): "กล่อง (กล่องละ 10 แพ็ค)" for a label that
// carries its contents, "กิโลกรัม" for a plain one - never a fixed word
function _ohbUnitWord(l) { return String(l.unit_word || '').trim(); }
function _ohbUnitFull(l) {
  const w = _ohbUnitWord(l);
  if (!w) return '';
  const m = String(l.unit_label || '').trim().match(/^[^\s()\[\]（）0-9๐-๙]+\s*([\s\S]*)$/);
  const rest = m ? m[1].trim().replace(/^[(（]\s*/, '').replace(/\s*[)）]$/, '').trim() : '';
  return rest ? `${w} (${w}ละ ${rest})` : w;
}
function ohbRenderDetail() {
  const o = _ohbOrder;
  const work = _ohbCanWork() && ['submitted', 'processed'].includes(o.status);
  const reasons = (_ohbQ && _ohbQ.reasons) || [];
  const phone = _ohbPhone();
  const shown = o.lines.filter(l => (l.confirmed_units || 0) > 0 || (l.suggested_units || 0) > 0 || (l.processed_units || 0) > 0);
  const blocks = shown.map(l => {          // PR-M5, a phone: one block per line — name, สั่ง, ส่งจริง (44 px), reason, note
    const sent = l.processed_units != null ? l.processed_units : l.confirmed_units;
    const opts = reasons.map(r => `<option value="${r.code}"${l.ss_reason === r.code ? ' selected' : ''}>${esc(r.label)}</option>`).join('');
    return `<div class="ohb-line">
      <div class="ohb-line-n">${esc(l.name || l.code)}</div>
      <div class="ohb-line-c">${esc(l.code)}</div>
      <div class="ohb-line-q">สั่ง <b>${_bmcNum(l.confirmed_units)}</b> ${esc(_ohbUnitFull(l))} <span style="color:var(--text-muted)">· ยอดแนะนำ ${_bmcNum(l.suggested_units)}</span>${l.reason_th ? `<div style="color:var(--amber-ink)">${esc(l.reason_th)}${l.note ? ' · ' + esc(l.note) : ''}</div>` : ''}</div>
      ${work ? `<label class="ohb-line-f">ส่งจริง <input inputmode="decimal" id="ohbP_${esc(l.code)}" value="${esc(String(sent ?? ''))}" oninput="ohbPLineCheck(${_bmcQ(l.code)})" aria-label="ส่งจริง ${esc(l.name || l.code)}" class="ohb-sent"> ${esc(_ohbUnitWord(l))}</label>
        <div id="ohbPE_${esc(l.code)}" class="ohb-line-e" role="alert"></div>
        <select id="ohbR_${esc(l.code)}" onchange="ohbPLineFull(${_bmcQ(l.code)})" class="ohb-line-r" aria-label="เหตุผลของ ${esc(l.name || l.code)}"><option value="">— เหตุผล (หากส่งไม่ตรง) —</option>${opts}</select>
        <input id="ohbN_${esc(l.code)}" value="${esc(l.ss_note || '')}" placeholder="หมายเหตุ" class="ohb-line-r" aria-label="หมายเหตุของ ${esc(l.name || l.code)}">`
        : `<div class="ohb-line-q">ส่งจริง <b>${_bmcNum(sent)}</b> ${esc(_ohbUnitWord(l))}${l.ss_reason_th ? ` <span style="color:var(--amber-ink)">· ${esc(l.ss_reason_th)}${l.ss_note ? ' · ' + esc(l.ss_note) : ''}</span>` : ''}</div>`}
      ${l.received_units != null ? `<div class="ohb-line-c">รับจริง ${_bmcNum(l.received_units)} ${esc(_ohbUnitWord(l))}</div>` : ''}</div>`;
  }).join('');
  const rows = shown.map(l => {
    const sent = l.processed_units != null ? l.processed_units : l.confirmed_units;
    return `<tr style="border-top:1px solid var(--border)">
      <td class="py-1 pr-2">${esc(l.name || l.code)}<div class="text-[10.5px]" style="color:var(--text-muted)">${esc(l.code)} · หน่วยสั่ง ${esc(_ohbUnitFull(l))}</div></td>
      <td class="py-1 pr-2 text-right">${_bmcNum(l.suggested_units)}</td>
      <td class="py-1 pr-2 text-right"><b>${_bmcNum(l.confirmed_units)}</b>${l.reason_th ? `<div class="text-[10.5px]" style="color:var(--amber-ink)">${esc(l.reason_th)}${l.note ? ' · ' + esc(l.note) : ''}</div>` : ''}</td>
      <td class="py-1 pr-2 text-right">${work ? `<input inputmode="decimal" id="ohbP_${esc(l.code)}" value="${esc(String(sent ?? ''))}" oninput="ohbPLineCheck(${_bmcQ(l.code)})" aria-label="ส่งจริง ${esc(l.name || l.code)}" style="width:64px;text-align:center;border:1px solid var(--border-control);border-radius:6px;padding:3px;background:var(--bg-input);color:var(--text-primary)">
        <div id="ohbPE_${esc(l.code)}" class="text-[11px]" role="alert" style="color:var(--bad-ink);text-align:right"></div>` : _bmcNum(sent)}</td>
      <td class="py-1">${work ? `<select id="ohbR_${esc(l.code)}" onchange="ohbPLineFull(${_bmcQ(l.code)})" style="font-size:11px;border:1px solid var(--border-control);border-radius:6px;background:var(--bg-input);color:var(--text-primary)"><option value="">— เหตุผล (หากส่งไม่ตรง) —</option>${reasons.map(r => `<option value="${r.code}"${l.ss_reason === r.code ? ' selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
        <input id="ohbN_${esc(l.code)}" value="${esc(l.ss_note || '')}" placeholder="หมายเหตุ" style="width:110px;font-size:11px;border:1px solid var(--border-control);border-radius:6px;padding:2px 4px;background:var(--bg-input);color:var(--text-primary)">`
        : (l.ss_reason_th ? `<span class="text-[11px]" style="color:var(--amber-ink)">${esc(l.ss_reason_th)}${l.ss_note ? ' · ' + esc(l.ss_note) : ''}</span>` : '')}
        ${l.received_units != null ? `<div class="text-[10.5px]" style="color:var(--text-muted)">รับจริง ${_bmcNum(l.received_units)} ${esc(_ohbUnitWord(l))}</div>` : ''}</td></tr>`;
  }).join('');
  document.getElementById('ohbDetail').innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">${phone ? '' : '#' + o.order_id + ' '}${esc(o.branch || '')} · ${esc(_OHB_RND[o.round] || o.round)} · ตัดรอบ ${esc(fmtDay(o.cycle_date))} · วันส่ง ${esc(fmtDay(o.delivery))}${o.late ? ' ' + _bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)') : ''}</div>
      ${_ohbBtn('ปิด', "document.getElementById('ohbDetail').style.display='none'")}</div>
    <div class="text-xs mt-1" style="color:var(--text-secondary)">${_bmcChip(o.status_th, o.status === 'submitted' ? 'var(--amber-ink)' : 'var(--good-ink)')}
      <b>${esc(VALUE_ORDERED_TH)} ฿${_bmcNum(o.value_ordered, 0)}</b>${o.value_sent != null ? ` · <b>${esc(VALUE_SENT_TH)} ฿${_bmcNum(o.value_sent, 0)}</b>` : ''} ·
      ${o.stock_count_id ? 'ยอดแนะนำหักคงเหลือตามระบบแล้ว' : 'สาขายังไม่มีผลตรวจนับสิ้นเดือน ยอดแนะนำจึงไม่หักคงเหลือ'}${o.note ? ' · ' + esc(o.note) : ''}${o.processed_by ? ` · คลังยืนยันโดย ${esc(whoTh(o.processed_by))}` : ''}${o.accounting_keyed_at ? ` · คีย์บัญชีแล้ว ${esc(fmtDay(o.accounting_keyed_date || ''))} โดย ${esc(whoTh(o.accounting_keyed_by))}` : ''}</div>
    ${(o.claims || []).length ? `<div class="text-xs mt-1" style="color:var(--text-secondary)">เคลมของใบสั่งซื้อนี้: ${o.claims.map(c => `#${c.claim_id} ${esc(c.origin_th || '')} · ${esc(c.status_th || '')}`).join(' ; ')}</div>` : ''}
    ${o.buffer_pct ? `<div class="text-xs mt-1" style="color:var(--navy-ink)">สาขาปรับยอดพยากรณ์ ${o.buffer_pct > 0 ? '+' : ''}${_bmcNum(o.buffer_pct, 0)}% · ${esc(o.buffer_reason_th || '')}${o.buffer_note ? ' · ' + esc(o.buffer_note) : ''} (ยอดแนะนำในตารางคือยอดปกติก่อนปรับ)</div>` : ''}
    ${phone ? `<div class="mt-2">${blocks}</div>` : `<div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
      <th class="py-1 pr-2">วัตถุดิบ</th><th class="py-1 pr-2 text-right">ยอดแนะนำ</th><th class="py-1 pr-2 text-right">สาขายืนยัน</th><th class="py-1 pr-2 text-right">ส่งจริง</th><th class="py-1">เหตุผล (คลัง)</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`}
    ${!work && o.status === 'submitted' ? '<div class="text-xs mt-2" style="color:var(--text-muted)">คลังเป็นผู้ระบุยอดส่งจริง</div>' : ''}
    ${work ? `<div class="flex flex-wrap items-center gap-2 mt-2 text-xs${phone ? ' ohb-actbar' : ''}">เลขที่ PO <input id="ohbPo" value="${esc(o.po_ref || '')}" style="width:150px;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 8px;font-size:${phone ? 16 : 13}px;min-height:${phone ? 44 : 0}px;background:var(--bg-input);color:var(--text-primary)">
      ${phone ? `<button onclick="ohbProcess()" class="brn-act">${o.status === 'processed' ? 'บันทึกการแก้ไข' : 'ยืนยันยอดส่ง'}</button>` : _ohbBtn(o.status === 'processed' ? 'บันทึกการแก้ไข' : 'ยืนยันยอดส่ง', 'ohbProcess()', true)}<span id="ohbDStatus"></span></div>` : ''}`;
}
// The typed ส่งจริง of one line: '' = left as the branch confirmed; otherwise a number not below 0. -> {q, err}
function _ohbPRead(code) {
  const el = document.getElementById('ohbP_' + code);
  if (!el) return { q: null, err: '' };
  const raw = String(el.value).trim().replace(',', '.');
  if (raw === '') return { q: null, err: '' };
  const q = Number(raw);
  if (!Number.isFinite(q) || !/^-?\d+(\.\d+)?$/.test(raw)) return { q: null, err: 'ใส่ตัวเลข' };
  if (q < 0) return { q: null, err: 'ต้องไม่ติดลบ' };
  return { q, err: '' };
}
// the red note under a line: a wrong number, or a changed quantity without its reason (the server asks for the same)
function _ohbPLine(l) {
  const { q, err } = _ohbPRead(l.code);
  let msg = err;
  if (!msg && q !== null && Math.abs(q - Number(l.confirmed_units || 0)) > 1e-9) {
    const r = document.getElementById('ohbR_' + l.code), n = document.getElementById('ohbN_' + l.code);
    if (!r || !r.value) msg = 'เลือกเหตุผลที่ส่งไม่ตรงกับที่สาขายืนยัน';
    else if (r.value === 'other' && !(n && n.value.trim())) msg = 'ระบุคำอธิบายของเหตุผล “อื่น ๆ”';
  }
  const note = document.getElementById('ohbPE_' + l.code), inp = document.getElementById('ohbP_' + l.code);
  if (note) note.textContent = msg;
  if (inp) inp.style.borderColor = msg ? 'var(--bad-ink)' : 'var(--border-control)';
  return msg;
}
function ohbPLineFull(code) { const l = _ohbOrder && _ohbOrder.lines.find(x => x.code === code); if (l) _ohbPLine(l); }
function ohbPLineCheck(code) {            // while typing: only a wrong number is shown (the reason is asked at the confirm)
  const { err } = _ohbPRead(code);
  const note = document.getElementById('ohbPE_' + code), inp = document.getElementById('ohbP_' + code);
  if (note) note.textContent = err;
  if (inp) inp.style.borderColor = err ? 'var(--bad-ink)' : 'var(--border-control)';
}
async function ohbProcess() {
  const o = _ohbOrder;
  const bad = o.lines.filter(l => document.getElementById('ohbP_' + l.code)).map(l => [l, _ohbPLine(l)]).filter(([, m]) => m);
  if (bad.length) {                       // nothing is saved until every line is right
    bmcStatus('ohbDStatus', 'warn', `ไม่ได้บันทึก · แก้ ${bad.length} รายการที่มีข้อความสีแดง`);
    const first = document.getElementById('ohbPE_' + bad[0][0].code);
    if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }
  const lines = o.lines.map(l => {
    const r = document.getElementById('ohbR_' + l.code);
    const n = document.getElementById('ohbN_' + l.code);
    return { code: l.code, processed_units: _ohbPRead(l.code).q, reason: r ? r.value || null : null, note: n ? n.value || null : null };
  });
  const po = (document.getElementById('ohbPo').value || '').trim();
  const edit = o.status === 'processed';
  const sent = o.lines.filter(l => (l.confirmed_units || 0) > 0 || (l.processed_units || 0) > 0 || (l.suggested_units || 0) > 0);
  const changed = lines.filter((x, i) => x.processed_units !== null && Math.abs(x.processed_units - Number(o.lines[i].confirmed_units || 0)) > 1e-9).length;
  if (!await uiConfirm(`${edit ? 'บันทึกการแก้ไขยอดส่ง' : 'ยืนยันการจัดส่ง'} · ใบสั่งซื้อ #${o.order_id} · สาขา${o.branch || ''} · ${sent.length} รายการ · ${po ? 'PO ' + po : 'ไม่ระบุ PO'}\n`
      + `${VALUE_ORDERED_TH} ฿${_bmcNum(o.value_ordered, 0)}${changed ? ` · ส่งไม่ตรงกับที่สาขายืนยัน ${changed} รายการ` : ' · ส่งตรงกับที่สาขายืนยันทุกรายการ'}`
      + (edit ? '' : '\nเมื่อยืนยันแล้ว สาขาเห็นว่าคลังจัดส่งแล้วและรับสินค้าได้'),
      { title: edit ? 'ยืนยันการแก้ไขยอดส่ง' : 'ยืนยันการจัดส่ง', ok: edit ? 'บันทึกการแก้ไข' : 'ยืนยันยอดส่ง', cancel: 'ยังไม่ยืนยัน' })) { bmcStatus('ohbDStatus', null, 'ยังไม่ยืนยัน'); return; }
  try {
    const r = await _bmcJson(`${API}/api/bom-center/hub/process/${o.order_id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines, po_ref: po || null }) });
    await ohbOpen(o.order_id);            // the detail stays open, now with the status คลังจัดส่งแล้ว
    // the list keeps the order too, with its new status and its sent value; it is filtered again on the next load
    const row = _ohbQ && _ohbQ.orders.find(x => x.order_id === o.order_id);
    if (row) { Object.assign(row, { status: 'processed', po_ref: po || null, value: r.value, value_sent: r.value_sent, value_ordered: r.value_ordered, value_label: r.value_label }); ohbRenderQueueList(); }
    bmcStatus('ohbDStatus', true, `${edit ? 'บันทึกการแก้ไขยอดส่งแล้ว' : 'ยืนยันยอดส่งแล้ว · คลังจัดส่งแล้ว'} · ${r.value_label} ฿${_bmcNum(r.value, 0)}`);
  } catch (e) { bmcStatus('ohbDStatus', false, String(e.message || e).slice(0, 220)); }
}
async function ohbShowConsolidated() {
  const box = document.getElementById('ohbConsolidated');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังรวมข้อมูล…</span>';
  try {
    const c = await _bmcJson(`${API}/api/bom-center/hub/consolidated?${_ohbExportQS()}`);
    box.innerHTML = `<div style="display:flex;justify-content:space-between"><div class="text-sm font-semibold" style="color:var(--text-primary)">สรุปรวมตามวันส่ง (${esc(c.status_th || '')}) · สั่งจาก ${esc(c.supplier || '')}</div>${_ohbBtn('ปิด', "document.getElementById('ohbConsolidated').style.display='none'")}</div>` +
      (c.groups.length ? c.groups.map(g => `<div class="text-xs font-semibold mt-3" style="color:var(--text-primary)">วันส่ง ${esc(fmtDay(g.delivery))}</div>
        <div style="overflow-x:auto"><table class="w-full text-xs"><tbody>${g.items.map(it => `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(it.name)} <span style="color:var(--text-muted)">${esc(it.code)}</span></td>
          <td class="py-1 pr-2 text-right"><b>${_bmcNum(it.total)}</b> ${esc(it.unit || '')}</td>
          <td class="py-1" style="color:var(--text-muted)">${Object.entries(it.by_branch).map(([b, q]) => `${esc(b)} ${_bmcNum(q)}`).join(' · ')}</td></tr>`).join('')}</tbody></table></div>`).join('')
        : '<div class="text-xs mt-2" style="color:var(--text-muted)">ไม่มีใบสั่งซื้อในช่วงนี้</div>');
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function ohbDownloadConsolidated() {
  bmcDownload(`${API}/api/bom-center/hub/consolidated.xlsx?${_ohbExportQS()}`, 'สรุปใบสั่งซื้อตามวันส่ง.xlsx', 'ohbQStatus');
}

// ── compliance ───────────────────────────────────────────────────────────────
const _OHB_CS = { ok: ['ตามแนะนำ', 'var(--good-ink)'], over: ['สั่งเกิน', 'var(--amber-ink)'], under: ['สั่งขาด', 'var(--amber-ink)'],
  not_ordered: ['ไม่ได้สั่ง', 'var(--bad-ink)'], not_submitted: ['ยังไม่ส่ง', 'var(--bad-ink)'], upcoming: ['ยังไม่ถึงกำหนด', 'var(--text-muted)'] };
function _ohbCRange() { return `date_from=${document.getElementById('ohbCFrom').value}&date_to=${document.getElementById('ohbCTo').value}`; }
async function ohbLoadComp() {
  const box = document.getElementById('ohbComp');
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    const c = await _bmcJson(`${API}/api/bom-center/hub/compliance?${_ohbCRange()}`);
    document.getElementById('ohbCChips').innerHTML = Object.entries(_OHB_CS).filter(([k]) => c.counts[k]).map(([k, [t, ink]]) => _bmcChip(`${t} ${c.counts[k]}`, ink)).join(' ');
    const pct = v => v == null ? '' : `${v > 0 ? '+' : ''}${_bmcNum(v, 1)}%`;
    box.innerHTML = c.rows.length ? `<div style="overflow-x:auto"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
        <th class="py-1 pr-2">ตัดรอบ</th><th class="py-1 pr-2">สาขา</th><th class="py-1 pr-2">รอบส่ง</th><th class="py-1 pr-2">สถานะ</th>
        <th class="py-1 pr-2 text-right">ยอดแนะนำ ฿</th><th class="py-1 pr-2 text-right">สาขายืนยัน ฿</th><th class="py-1 pr-2 text-right">ส่งจริง ฿</th><th class="py-1 pr-2 text-right">รับจริง ฿</th><th class="py-1">เวลา</th></tr></thead><tbody>` +
      c.rows.map(r => { const [t, ink] = _OHB_CS[r.status] || [r.status, 'var(--text-muted)'];
        return `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(fmtDay(r.cycle_date))}</td><td class="py-1 pr-2">${esc(r.branch || '')}</td><td class="py-1 pr-2">${esc(_OHB_RND[r.round] || r.round)}</td>
          <td class="py-1 pr-2">${_bmcChip(t, ink)}</td>
          <td class="py-1 pr-2 text-right">${r.suggested != null ? _bmcNum(r.suggested, 0) : ''}</td>
          <td class="py-1 pr-2 text-right">${r.confirmed != null ? _bmcNum(r.confirmed, 0) : ''} <span style="color:var(--text-muted)">${pct(r.confirmed_vs_suggested_pct)}</span></td>
          <td class="py-1 pr-2 text-right">${r.processed != null ? _bmcNum(r.processed, 0) : ''} <span style="color:var(--text-muted)">${pct(r.centre_change_pct)}</span></td>
          <td class="py-1 pr-2 text-right">${r.received != null ? _bmcNum(r.received, 0) : ''} <span style="color:var(--text-muted)">${pct(r.delivery_gap_pct)}</span></td>
          <td class="py-1">${r.order_id ? (r.late ? _bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)') : '') + (r.processed_on_time === false ? _bmcChip('ยืนยันยอดส่งช้า', 'var(--bad-ink)') : '') : ''}</td></tr>`; }).join('') + '</tbody></table></div>'
      : '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีข้อมูล (สาขายังไม่เริ่มใช้งาน)</span>';
    if (c.issues && c.issues.length) box.innerHTML += `<div class="text-sm font-semibold mt-4 mb-1" style="color:var(--text-primary)">การทำใบตัดเบิกในช่วงวันเดียวกัน</div>${ohbIsSummaryTable(c.issues)}`;
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function ohbDownloadComp() { bmcDownload(`${API}/api/bom-center/hub/compliance.xlsx?${_ohbCRange()}`, 'ตรวจสอบการสั่ง.xlsx', 'ohbCStatus'); }

// ── monthly ──────────────────────────────────────────────────────────────────
async function ohbLoadMonth() {
  const box = document.getElementById('ohbMonthBody');
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังคำนวณ…</span>';
  try {
    const m = await _bmcJson(`${API}/api/bom-center/hub/monthly?month=${_ohbMonthVal()}`);
    const pct = v => v == null ? '—' : `${v > 0 ? '+' : ''}${_bmcNum(v, 1)}%`;
    const t = m.total;
    box.innerHTML = m.branches.length ? `<div class="text-sm" style="color:var(--text-primary)">${fmtMonthTh(m.month)} · ตามสูตร ฿${_bmcNum(t.bom_value, 0)} · ผลต่าง <b style="color:${t.gap_value > 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">฿${_bmcNum(t.gap_value, 0)} (${pct(t.gap_pct)})</b> · ใช้เกินสูตร ฿${_bmcNum(t.over_value, 0)}
        ${m.previous && m.previous.total.bom_value ? ` <span class="text-xs" style="color:var(--text-muted)">เดือนก่อน ${pct(m.previous.total.gap_pct)}</span>` : ''}</div>
      <div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left"><th class="py-1 pr-2">สาขา</th><th class="py-1 pr-2 text-right">งวด</th>
        <th class="py-1 pr-2 text-right">ตามสูตร ฿</th><th class="py-1 pr-2 text-right">ผลต่าง ฿</th><th class="py-1 pr-2 text-right">%</th><th class="py-1 pr-2 text-right">A / B / C ฿</th><th class="py-1 pr-2 text-right">เดือนก่อน</th><th class="py-1">ค้าง</th></tr></thead><tbody>` +
      m.branches.map(b => `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2 font-semibold">${esc(b.branch)}</td><td class="py-1 pr-2 text-right">${b.periods}</td>
        <td class="py-1 pr-2 text-right">${_bmcNum(b.bom_value, 0)}</td><td class="py-1 pr-2 text-right" style="color:${b.gap_value > 0 ? 'var(--bad-ink)' : 'inherit'}">${_bmcNum(b.gap_value, 0)}</td>
        <td class="py-1 pr-2 text-right">${pct(b.gap_pct)}</td><td class="py-1 pr-2 text-right">${['A', 'B', 'C'].map(k => _bmcNum(b.by_class[k], 0)).join(' / ')}</td>
        <td class="py-1 pr-2 text-right" style="color:var(--text-muted)">${pct(b.prev_gap_pct)}</td>
        <td class="py-1">${b.needs_reason ? _bmcChip(`ยังไม่ระบุเหตุผล ${b.needs_reason}`, 'var(--amber-ink)') : ''}${b.complete < b.periods ? _bmcChip('ยังไม่ยืนยันรับสินค้าครบ', 'var(--text-muted)') : ''}</td></tr>`).join('') + '</tbody></table></div>' +
      (m.top_reasons.length ? `<div class="text-xs font-semibold mt-3" style="color:var(--text-primary)">เหตุผลหลัก</div>` + m.top_reasons.map(r => `<div class="text-xs" style="border-top:1px solid var(--border);padding:3px 0">${esc(r.label)} · ${r.items} รายการ · ฿${_bmcNum(r.value, 0)}</div>`).join('') : '')
      : `<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีงวดที่ปิดในเดือน${fmtMonthTh(m.month)}</span>`;
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

// ── pilot (S10) ──────────────────────────────────────────────────────────────
async function ohbLoadPilot() {
  const rb = document.getElementById('ohbPilotReady'), sb = document.getElementById('ohbPilotScore');
  rb.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังตรวจ…</span>';
  sb.innerHTML = '';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/hub/pilot`);
    const R = d.readiness;
    const step = s => `<div class="text-xs py-0.5" style="color:${s.done ? 'var(--good-ink)' : 'var(--amber-ink)'}">${s.done ? '✓' : '•'} ${esc(s.todo)}</div>`;
    rb.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">ความพร้อม</div>
      <div class="text-xs mb-1" style="color:var(--text-muted)">ข้อมูลตั้งต้น: ทะเบียน ${_bmcNum(R.centre.ingredients, 0)} รายการ · ราคาเดือน ${esc((R.centre.price_months || []).map(fmtMonthTh).join(', ') || '—')} · สูตรกึ่งสำเร็จ ${R.centre.sub_recipes} · เวอร์ชันที่ประกาศใช้ ${R.centre.releases}</div>
      ${R.centre_steps.map(step).join('')}
      ${R.branches.map(b => `<div style="border-top:1px solid var(--border);padding:6px 0;margin-top:4px">
        <div class="text-sm"><b>${esc(b.branch)}</b> ${b.ready ? _bmcChip('พร้อม', 'var(--good-ink)') : _bmcChip('ยังไม่พร้อม', 'var(--amber-ink)')}
          <span class="text-xs" style="color:var(--text-muted)">${b.accounts.length ? 'ผู้ใช้: ' + b.accounts.map(a => esc(a.username)).join(', ') : ''}${b.first_count ? ` · ตรวจนับครั้งแรก ${esc(fmtDay(b.first_count))} · ล่าสุด ${esc(fmtDay(b.last_count))} (${b.counts} ครั้ง)` : ''}${b.last_order ? ` · ใบสั่งซื้อล่าสุด ${esc(fmtDay(b.last_order))}` : ''}</span></div>
        ${b.steps.map(step).join('')}</div>`).join('')}
      ${R.missing_branches.length ? `<div class="text-xs mt-1" style="color:var(--bad-ink)">ไม่พบสาขาในระบบ: ${R.missing_branches.map(esc).join(', ')}</div>` : ''}`;
    const T = d.scorecard.targets;
    const cell = (v, ok, unit) => v == null ? '<span style="color:var(--text-muted)">—</span>'
      : `<b style="color:${ok === false ? 'var(--bad-ink)' : ok ? 'var(--good-ink)' : 'var(--text-primary)'}">${_bmcNum(v, 1)}${unit}</b>`;
    const vd = { pass: ['ผ่าน', 'var(--good-ink)'], fail: ['ยังไม่ผ่าน', 'var(--bad-ink)'], pending: ['รอข้อมูล', 'var(--text-muted)'], not_started: ['ยังไม่เริ่ม', 'var(--text-muted)'] };
    sb.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">ผลนำร่อง ${d.scorecard.weeks} สัปดาห์ล่าสุด</div>
      <div style="overflow-x:auto"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
        <th class="py-1 pr-2">สาขา</th><th class="py-1 pr-2 text-right">ใบตัดเบิกครบทุกวัน (≥${T.issue_coverage}%)</th><th class="py-1 pr-2 text-right">ยืนยันภายในวัน (≥${T.issue_on_time}%)</th>
        <th class="py-1 pr-2 text-right">ผลต่างมีเหตุผล (≥${T.reason_coverage}%)</th><th class="py-1 pr-2 text-right">พยากรณ์กลุ่มมูลค่าสูง (≤${T.mape_a}%)</th><th class="py-1 pr-2 text-right">ส่งใบสั่งซื้อ</th><th class="py-1">สรุป</th></tr></thead><tbody>` +
      d.scorecard.branches.map(s => { const m = s.metrics, p = s.passes, [vt, vi] = vd[s.verdict];
        return `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2 font-semibold">${esc(s.branch)}<div class="text-[10.5px]" style="color:var(--text-muted)">${esc(fmtDay(s.from))} → ${esc(fmtDay(s.to))} · ${s.cycles} วันสั่ง · ใบตัดเบิก ${(s.slips ? s.slips.confirmed + s.slips.late : 0)} ใบ</div></td>
          <td class="py-1 pr-2 text-right">${cell(m.issue_coverage, p.issue_coverage, '%')}</td><td class="py-1 pr-2 text-right">${cell(m.issue_on_time, p.issue_on_time, '%')}</td>
          <td class="py-1 pr-2 text-right">${cell(m.reason_coverage, p.reason_coverage, '%')}</td><td class="py-1 pr-2 text-right">${cell(m.mape_a, p.mape_a, '%')}</td>
          <td class="py-1 pr-2 text-right">${cell(m.order_submission, null, '%')}</td><td class="py-1">${_bmcChip(vt, vi)}</td></tr>`; }).join('') + '</tbody></table></div>';
  } catch (e) { rb.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

// ── ผลต่างตัดเบิก (PR-J3): the renderers (isr*) are shared with the branch page (14-branch.js) ───────────────
// daily: the confirmed issue (฿) against the BoM of what was sold, flags for a class-A item over the BoM on
// consecutive days · recon: the system stock (begin + received − issued) against the month-end count.
const _ISR_SLIP_INK = { on_time: 'var(--good-ink)', late: 'var(--amber-ink)', draft: 'var(--amber-ink)', missing: 'var(--bad-ink)', closed: 'var(--text-muted)' };
const _ISR_ST = { ok: ['ตรงกัน (ในเกณฑ์)', 'var(--good-ink)'], over: ['นับได้มากกว่าระบบ', 'var(--amber-ink)'],
  under: ['นับได้น้อยกว่าระบบ', 'var(--bad-ink)'], not_counted: ['ตรวจนับไม่ครบ', 'var(--text-muted)'] };
function _isrDm(iso) { const p = String(iso || '').split('-'); return p.length === 3 ? `${Number(p[2])}/${Number(p[1])}` : String(iso || ''); }
// 5.2: the value class as the floor and Ops read it
const _ISR_CLASS_TH = { A: 'กลุ่มมูลค่าสูง', B: 'กลุ่มมูลค่ากลาง', C: 'กลุ่มมูลค่าต่ำ' };
function _isrClassTh(c) { return _ISR_CLASS_TH[c] || String(c == null ? '' : c); }
function _isrPct(v) { return v == null ? '—' : `${v > 0 ? '+' : ''}${_bmcNum(v, 1)}%`; }
function _isrMoney(v) { return v == null ? '—' : `${v < 0 ? '−' : ''}฿${_bmcNum(Math.abs(v), 0)}`; }
function _isrBaht(v) { return v == null ? '<span style="color:var(--text-muted)">—</span>' : _bmcNum(v, 0); }
function isrFlagChips(d) {
  const t = d.totals;
  const chips = [_bmcChip(`ใบตัดเบิกยืนยันแล้ว ${t.confirmed_days}/${t.trading_days} วัน`, t.trading_days && t.confirmed_days >= t.trading_days ? 'var(--good-ink)' : 'var(--amber-ink)')];
  if (t.late_days) chips.push(_bmcChip(`ยืนยันล่าช้า ${t.late_days} วัน`, 'var(--amber-ink)'));
  if (t.draft_days) chips.push(_bmcChip(`ยังไม่ยืนยัน ${t.draft_days} วัน`, 'var(--amber-ink)'));
  if (t.missing_days) chips.push(_bmcChip(`ไม่มีใบ ${t.missing_days} วัน`, 'var(--bad-ink)'));
  if (!d.flags.length) chips.push(_bmcChip('✓ ไม่พบการเบิกเกินสูตรต่อเนื่อง', 'var(--good-ink)'));
  d.flags.forEach(f => chips.push(_bmcChip(`${f.name} เบิกเกินสูตร ${f.days} วันติดต่อกัน (สูงสุด ${_isrPct(f.max_pct)}) ${_isrDm(f.from)}–${_isrDm(f.to)}`, 'var(--bad-ink)')));
  return chips.join(' ');
}
function isrDailyTable(d, openFn) {
  if (!d.rows.length) return '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มียอดขายหรือใบตัดเบิกในเดือนนี้</span>';
  const t = d.totals;
  const th = (s, r) => `<th class="py-1 pr-2${r ? ' text-right' : ''}">${s}</th>`;
  const body = d.rows.map(r => {
    const ink = r.var_pct != null && r.var_pct > 0 ? 'var(--bad-ink)' : 'var(--text-primary)';
    return `<tr style="border-top:1px solid var(--border);cursor:pointer${r.flagged ? ';background:var(--bg-input)' : ''}" onclick="${openFn}('${r.date}')">
      <td class="py-1 pr-2" style="white-space:nowrap"><a href="#" onclick="return false" style="color:var(--accent);font-weight:600">${_isrDm(r.date)}</a> ${esc(r.weekday_th)}</td>
      <td class="py-1 pr-2">${_bmcChip(r.slip_status_th, _ISR_SLIP_INK[r.slip_status] || 'var(--text-muted)')}</td>
      <td class="py-1 pr-2 text-right">${_isrBaht(r.used_value)}</td><td class="py-1 pr-2 text-right">${_isrBaht(r.waste_value)}</td>
      <td class="py-1 pr-2 text-right">${_isrBaht(r.issued_value)}</td><td class="py-1 pr-2 text-right">${r.trading ? _isrBaht(r.bom_value) : '<span style="color:var(--text-muted)">—</span>'}</td>
      <td class="py-1 pr-2 text-right" style="color:${ink}">${_isrBaht(r.var_value)}</td><td class="py-1 pr-2 text-right" style="color:${ink}">${_isrPct(r.var_pct)}</td>
      <td class="py-1">${r.over_items.map(o => `${esc(o.name)} ${_isrPct(o.var_pct)}`).join(', ')}${r.flagged ? ' ' + _bmcChip('เบิกเกินต่อเนื่อง', 'var(--bad-ink)') : ''}</td></tr>`;
  }).join('');
  return `<div style="overflow-x:auto"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
      ${th('วันที่')}${th('ใบตัดเบิก')}${th('เบิกใช้ ฿', 1)}${th('ของเสีย ฿', 1)}${th('รวมเบิก ฿', 1)}${th('ตามสูตร ฿', 1)}${th('ผลต่าง ฿', 1)}${th('ผลต่าง %', 1)}${th('กลุ่มมูลค่าสูงที่เบิกเกินเกณฑ์')}</tr></thead>
    <tbody>${body}</tbody>
    <tfoot><tr style="border-top:2px solid var(--border);font-weight:600"><td class="py-1 pr-2" colspan="2">รวม (เฉพาะวันที่ยืนยันแล้ว)</td>
      <td class="py-1 pr-2 text-right">${_isrBaht(t.used_value)}</td><td class="py-1 pr-2 text-right">${_isrBaht(t.waste_value)}</td><td class="py-1 pr-2 text-right">${_isrBaht(t.issued_value)}</td>
      <td class="py-1 pr-2 text-right">${_isrBaht(t.bom_value)}</td><td class="py-1 pr-2 text-right" style="color:${t.var_value > 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${_isrBaht(t.var_value)}</td>
      <td class="py-1 pr-2 text-right">${_isrPct(t.var_pct)}</td><td></td></tr></tfoot></table></div>
    <div class="text-[11px] mt-2" style="color:var(--text-muted)">วันที่ใบตัดเบิกยังไม่ยืนยันไม่มีตัวเลขเบิกให้เปรียบเทียบ · ราคาซื้อเดือน${fmtMonthTh(d.price_month)} · เกณฑ์เตือน: กลุ่มมูลค่าสูงเบิกเกินสูตรมากกว่า ${d.thresholds.over_pct}% ติดต่อกัน ${d.thresholds.streak_days} วันขายขึ้นไป</div>`;
}
function isrDayItems(d, closeJs) {
  const s = d.slip, conf = s.key === 'on_time' || s.key === 'late';
  const head = `<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:baseline">
      <b class="text-sm" style="color:var(--text-primary)">วัน${esc(d.weekday_th)}ที่ ${_isrDm(d.date)} · ${esc(d.branch.name || '')}</b>
      ${_bmcChip(s.status_th, _ISR_SLIP_INK[s.key] || 'var(--text-muted)')}
      ${conf ? `<span class="text-xs" style="color:var(--text-secondary)">ยืนยันเวลา ${esc(_brnIssTime(s.confirmed_at))} โดย ${esc(whoTh(s.confirmed_by))}</span>` : ''}
      ${closeJs ? `<button onclick="${closeJs}" style="margin-left:auto;font-size:12px;color:var(--text-muted);background:none;border:0;cursor:pointer">ปิด</button>` : ''}</div>`;
  const note = conf ? '' : '<div class="text-xs mt-2" style="color:var(--amber-ink)">ใบตัดเบิกวันนั้นยังไม่ยืนยัน · แสดงเฉพาะปริมาณตามสูตร</div>';
  const flags = d.flags.length ? `<div class="flex flex-wrap gap-1 mt-2">${d.flags.map(f => _bmcChip(`${f.name} เบิกเกินสูตร ${f.days} วันติดต่อกัน (สูงสุด ${_isrPct(f.max_pct)})`, 'var(--bad-ink)')).join(' ')}</div>` : '';
  const q = (v, u) => v == null ? '<span style="color:var(--text-muted)">—</span>' : `${_bmcNum(v, 1)} ${esc(u || '')}`;
  const th = (s, r) => `<th class="py-1 pr-2${r ? ' text-right' : ''}">${s}</th>`;
  const rows = d.rows.map(r => {
    const ink = r.var_pct != null && r.var_pct > 0 ? 'var(--bad-ink)' : 'var(--text-primary)';
    return `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2"><b>${esc(r.name)}</b><div class="text-[10.5px]" style="color:var(--text-muted)">${esc(r.code)} · ${esc(_isrClassTh(r.class))}${r.waste_reason_th ? ' · ของเสีย: ' + esc(r.waste_reason_th) + (r.note ? ' (' + esc(r.note) + ')' : '') : ''}</div></td>
      <td class="py-1 pr-2 text-right">${q(r.used, r.base_unit)}</td><td class="py-1 pr-2 text-right">${q(r.waste, r.base_unit)}</td><td class="py-1 pr-2 text-right">${q(r.issued, r.base_unit)}</td>
      <td class="py-1 pr-2 text-right">${q(r.bom, r.base_unit)}</td><td class="py-1 pr-2 text-right" style="color:${ink}">${r.var_qty == null ? '—' : (r.var_qty > 0 ? '+' : '') + _bmcNum(r.var_qty, 1)}</td>
      <td class="py-1 pr-2 text-right" style="color:${ink}">${_isrPct(r.var_pct)}</td><td class="py-1 pr-2 text-right" style="color:${ink}">${_isrBaht(r.var_value)}</td>
      <td class="py-1">${r.signal ? _bmcChip(r.signal_th, r.signal === 'streak' ? 'var(--bad-ink)' : 'var(--amber-ink)') : ''}</td></tr>`;
  }).join('');
  return head + note + flags + (d.rows.length ? `<div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
      ${th('วัตถุดิบ')}${th('เบิกใช้', 1)}${th('ของเสีย', 1)}${th('รวมเบิก', 1)}${th('ตามสูตร', 1)}${th('ผลต่าง', 1)}${th('ผลต่าง %', 1)}${th('ผลต่าง ฿', 1)}${th('สัญญาณ')}</tr></thead><tbody>${rows}</tbody></table></div>`
    : '<div class="text-xs mt-2" style="color:var(--text-muted)">ไม่มีรายการ</div>');
}
function isrReconHead(r) {
  const p = r.period, t = r.totals, sl = r.slips;
  const miss = sl.missing_days;
  return `<div style="color:var(--text-secondary)">${esc(fmtDay(p.begin_date))} → ${esc(fmtDay(p.end_date))} · ${p.days} วัน · ราคาเดือน${fmtMonthTh(p.month)}</div>
    <div class="flex flex-wrap gap-1 mt-1">
      ${_bmcChip(`ใบตัดเบิกยืนยันแล้ว ${sl.confirmed_days}/${sl.trading_days} วัน`, sl.trading_days && sl.confirmed_days >= sl.trading_days ? 'var(--good-ink)' : 'var(--amber-ink)')}
      ${miss.length ? _bmcChip(`ขาดใบตัดเบิก ${miss.length} วัน`, 'var(--bad-ink)') : ''}
      ${sl.draft_days.length ? _bmcChip(`ยังไม่ยืนยัน ${sl.draft_days.length} วัน`, 'var(--amber-ink)') : ''}
      ${r.status.receipts_confirmed ? _bmcChip('รับสินค้าครบแล้ว', 'var(--good-ink)') : _bmcChip('ยังไม่ยืนยันรับสินค้าครบ', 'var(--amber-ink)')}</div>
    <div class="mt-1 text-sm">ผลต่างรวม <b style="color:${t.diff_value < 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${_isrMoney(t.diff_value)}</b> ·
      นับได้น้อยกว่าระบบ ${_isrMoney(t.under_value)} · นับได้มากกว่าระบบ ${_isrMoney(t.over_value)} · เกินเกณฑ์ ${t.flagged} รายการ ·
      ยังไม่ระบุเหตุผล <b style="color:${t.needs_reason ? 'var(--amber-ink)' : 'var(--good-ink)'}">${t.needs_reason}</b>${t.not_counted ? ` · ตรวจนับไม่ครบ ${t.not_counted}` : ''}</div>
    ${miss.length ? `<div class="text-[11px] mt-1" style="color:var(--amber-ink)">ขาดใบตัดเบิก ${miss.length} วัน (${miss.slice(0, 6).map(_isrDm).join(', ')}${miss.length > 6 ? ' …' : ''}) คงเหลือตามระบบจึงอาจสูงกว่าที่เป็นจริง</div>` : ''}`;
}
// o: {all: show every row, edit: reason inputs, pfx: id prefix, save: name of the save function (code, true),
//     qty: optional (qtyBase, row) -> HTML text, a quantity in the unit the page's readers count in (the branch page and the
//     accounting page pass brnQtyUnit; the other centre pages pass nothing and keep the base unit),
//     brief: no movement formula on a row — the system stock, the count and the coloured difference only (the accounting page)}
function isrReconTable(r, o) {
  o = o || {};
  const rows = r.rows.filter(x => o.all || x.status !== 'ok').slice(0, o.all ? 500 : 80);
  if (!rows.length) return '<span class="text-xs" style="color:var(--good-ink)">✓ ทุกรายการอยู่ในเกณฑ์</span>';
  const pfx = o.pfx || 'isrRc';
  const q = (v, x) => v == null ? '—' : o.qty ? o.qty(v, x) : `${_bmcNum(v, 1)} ${esc(x.base_unit || '')}`;
  const sg = (v, x) => v == null ? '—' : o.qty ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${o.qty(Math.abs(v), x)}` : `${v > 0 ? '+' : ''}${_bmcNum(v, 1)}`;
  return rows.map(x => {
    const [label, ink] = _ISR_ST[x.status] || [x.status_th, 'var(--text-muted)'];
    const flag = x.status === 'over' || x.status === 'under';
    const opts = r.reasons.map(c => `<option value="${esc(c.code)}"${x.reason === c.code ? ' selected' : ''}>${esc(c.label)}</option>`).join('');
    return `<div style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:baseline"><b class="text-sm" style="color:var(--text-primary)">${esc(x.name)}</b>
        ${_bmcChip(label, ink)} <span class="text-[11px]" style="color:var(--text-muted)">${esc(x.code)} · ${esc(_isrClassTh(x.class))} (เกณฑ์ ±${r.thresholds[x.class]}%)</span>
        <span style="margin-left:auto" class="text-sm">${x.diff_value != null ? `<b style="color:${x.diff_value < 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${_isrMoney(x.diff_value)}</b>` : ''}</span></div>
      <div class="text-xs mt-0.5" style="color:var(--text-secondary)">${o.brief ? '' : `ยกมา ${q(x.begin, x)} + รับ ${q(x.received, x)} + เคลม/ปรับ ${q(x.claims, x)} − ตัดเบิก ${q(x.used, x)} − ของเสีย ${q(x.waste, x)}
        = `}คงเหลือตามระบบ <b>${q(x.system, x)}</b> · นับจริง <b>${q(x.count, x)}</b>${x.diff != null ? ` · ผลต่าง <b class="isr-diff" style="color:${x.diff < 0 ? 'var(--bad-ink)' : x.diff > 0 ? 'var(--amber-ink)' : 'var(--good-ink)'}">${sg(x.diff, x)}${x.diff_pct != null ? ` (${_isrPct(x.diff_pct)})` : ''}</b>` : ''}</div>
      ${x.status === 'not_counted' ? '<div class="text-xs mt-0.5" style="color:var(--text-muted)">ไม่พบรายการนี้ในผลตรวจนับต้นงวดหรือปลายงวด จึงยังเปรียบเทียบไม่ได้</div>' : ''}
      ${flag && o.edit ? `<div class="flex flex-wrap items-center gap-2 mt-1 text-xs${o.qty ? ' brn-reason' : ''}">
        <select id="${pfx}Rs_${esc(x.code)}" style="${o.qty ? 'flex:1 1 100%;' : ''}border:1px solid ${x.needs_reason ? 'var(--amber-ink)' : 'var(--border-control)'};border-radius:var(--r-sm);padding:4px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)"><option value="">— เหตุผล —</option>${opts}</select>
        <input id="${pfx}Rn_${esc(x.code)}" value="${esc(x.reason_note || '')}" placeholder="หมายเหตุ" style="flex:1 1 ${o.qty ? '0' : '140px'};min-width:0;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)">
        <button${o.qty ? ' class="brn-act brn-act-line"' : ''} onclick="${o.save}(${_bmcQ(x.code)}, true)" style="${o.qty ? '' : 'border:1px solid var(--border);border-radius:var(--r-sm);padding:3px 10px;font-size:12px;cursor:pointer;background:transparent;color:var(--text-primary)'}">บันทึก</button>
        ${x.reason_by ? `<span style="color:var(--text-muted)">${esc(whoTh(x.reason_by))}</span>` : ''}</div>
      <div id="${pfx}E_${esc(x.code)}" class="brn-err" role="alert"></div>`
      : (flag && x.reason_th ? `<div class="text-xs mt-1" style="color:var(--text-secondary)">เหตุผล: ${esc(x.reason_th)}${x.reason_note ? ' · ' + esc(x.reason_note) : ''}${x.reason_by ? ' · ' + esc(whoTh(x.reason_by)) : ''}</div>`
        : (flag ? '<div class="text-xs mt-1" style="color:var(--amber-ink)">ยังไม่ระบุเหตุผล</div>' : ''))}
    </div>`;
  }).join('');
}

// the centre's pane: ผลต่างตัดเบิกรายวัน
let _ohbIv = null;            // /api/bom-center/issue-reports/daily
async function ohbLoadIssueVar() {
  const sel = document.getElementById('ohbIvBranch');
  if (!sel) return;                         // no picker for a pinned login (centre page)
  const mon = { value: _ohbMonthVal() };
  const list = document.getElementById('ohbIvList'), chips = document.getElementById('ohbIvChips');
  document.getElementById('ohbIvDetail').style.display = 'none';
  try {
    // PR-M5: only the branches that have data in the month are listed
    const rows = await supplyBranchesWithData(mon.value);
    const cur = sel.value || String(supplyCtx().branch_id || '');
    sel.innerHTML = supplyBranchOptions(rows, '— เลือกสาขา —');
    if (rows.some(b => String(b.branch_id) === cur)) sel.value = cur;
    if (!sel.value) {
      chips.innerHTML = '';
      list.innerHTML = `<span class="text-xs" style="color:var(--text-muted)">${rows.length ? 'เลือกสาขา' : 'ยังไม่มีสาขาที่มีข้อมูลในเดือน' + esc(fmtMonthTh(mon.value))}</span>`;
      return;
    }
    supplyCtxSet({ branch_id: Number(sel.value) });
    list.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังคำนวณ…</span>';
    _ohbIv = await _bmcJson(`${API}/api/bom-center/issue-reports/daily?branch_id=${sel.value}&month=${mon.value}`);
    chips.innerHTML = isrFlagChips(_ohbIv);
    list.innerHTML = isrDailyTable(_ohbIv, 'ohbIvOpen');
  } catch (e) { list.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function ohbIvOpen(day) {
  const box = document.getElementById('ohbIvDetail');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/issue-reports/daily/day?branch_id=${document.getElementById('ohbIvBranch').value}&day=${day}`);
    box.innerHTML = isrDayItems(d, "document.getElementById('ohbIvDetail').style.display='none'");
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function ohbIvXlsx() {
  const b = document.getElementById('ohbIvBranch').value;
  if (!b) { _bmcStatus('ohbIvStatus', 'เลือกสาขาก่อนดาวน์โหลด', 'var(--amber-ink)'); return; }
  bmcDownload(`${API}/api/bom-center/issue-reports/daily.xlsx?branch_id=${b}&month=${_ohbMonthVal()}`, 'ผลต่างตัดเบิกรายวัน.xlsx', 'ohbIvStatus');
}

// ── ใช้วัตถุดิบต่อยอดขาย ฿100,000 (PR-V): the usage the forecast reads, set against the sales of the same days ──
let _ohbPs = null;            // /api/bom-center/per-sales
let _ohbPsAll = false;        // show every item, not the first 60
let _ohbPsView = 'bench';     // PR-X: the all-branch list reads against the benchmark (ค่ากลาง) or the plain total of every branch
const _OHB_PS_ROWS = 60;
function _ohbPsQS() {
  const sel = document.getElementById('ohbPsBranch'), w = document.getElementById('ohbPsWeeks');
  return `weeks=${w ? w.value : 4}${sel && sel.value ? '&branch_id=' + sel.value : ''}`;
}
function _ohbPsNote(it) {
  if (it.steady === false) return _bmcChip(`ใช้ไม่สม่ำเสมอ (แกว่ง ±${_bmcNum(it.swing_pct, 0)}%)`, 'var(--amber-ink)');
  if (it.steady === null && it.per_100k) return _bmcChip('ข้อมูลยังไม่พอ', 'var(--text-muted)');
  return it.swing_pct != null ? `<span class="text-[11px]" style="color:var(--text-muted)">±${_bmcNum(it.swing_pct, 0)}%</span>` : '';
}
function _ohbPsTable(items, note, withVs) {
  if (!items.length) return '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีข้อมูลการใช้ในช่วงนี้</span>';
  const shown = _ohbPsAll ? items : items.slice(0, _OHB_PS_ROWS);
  const num = v => v == null ? '<span style="color:var(--text-muted)">–</span>' : _bmcNum(v, v >= 100 ? 0 : v >= 10 ? 1 : 2);
  const chg = v => v == null ? '<span style="color:var(--text-muted)">–</span>'
    : `<span style="color:${Math.abs(v) >= 20 ? 'var(--amber-ink)' : 'var(--text-secondary)'}">${v > 0 ? '+' : ''}${_bmcNum(v, 1)}%</span>`;
  const vs = v => v == null ? '<span style="color:var(--text-muted)">–</span>'
    : `<span style="color:${Math.abs(v) >= 30 ? 'var(--amber-ink)' : 'var(--text-secondary)'}">${v > 0 ? '+' : ''}${_bmcNum(v, 1)}%</span>`;
  return `<div class="overflow-x-auto"><table class="text-xs w-full"><thead><tr class="text-left border-b" style="color:var(--text-muted)">
      <th class="pr-2">วัตถุดิบ</th><th class="pr-2">หน่วย</th><th class="pr-2 text-right">ต่อยอดขาย ฿100,000</th><th class="pr-2 text-right">ช่วงก่อนหน้า</th>
      <th class="pr-2 text-right">เปลี่ยนแปลง</th>${withVs ? '<th class="pr-2 text-right">การใช้ตามสูตรเทียบค่ากลาง</th>' : ''}<th class="pr-2 text-right">มูลค่า (฿)</th><th>${esc(note || 'ความสม่ำเสมอ')}</th></tr></thead>
    <tbody>${shown.map(it => `<tr class="border-b" style="border-color:var(--border-soft)"><td class="pr-2 py-1" title="${esc(it.code)}">${esc(it.name)}</td>
      <td class="pr-2" style="color:var(--text-secondary)">${esc(it.unit || '')}</td><td class="pr-2 text-right font-semibold">${num(it.per_100k)}</td>
      <td class="pr-2 text-right" style="color:var(--text-secondary)">${num(it.prev_per_100k)}</td><td class="pr-2 text-right">${chg(it.change_pct)}</td>
      ${withVs ? `<td class="pr-2 text-right">${vs(it.vs_benchmark_pct)}</td>` : ''}<td class="pr-2 text-right">${it.value_per_100k == null ? '<span style="color:var(--text-muted)">–</span>' : _bmcNum(it.value_per_100k, 0)}</td><td>${it.swing_pct === undefined ? '' : _ohbPsNote(it)}</td></tr>`).join('')}</tbody></table></div>
    ${items.length > _OHB_PS_ROWS ? `<div class="mt-2 text-xs"><a href="#" onclick="_ohbPsAll=!_ohbPsAll;ohbRenderPerSales();return false" style="color:var(--accent)">${_ohbPsAll ? 'แสดงเฉพาะ ' + _OHB_PS_ROWS + ' รายการแรก' : 'แสดงทั้งหมด ' + items.length + ' รายการ'}</a></div>` : ''}`;
}
function ohbRenderPerSales() {
  const d = _ohbPs, list = document.getElementById('ohbPsList'), chips = document.getElementById('ohbPsChips'), box = document.getElementById('ohbPsBranches');
  if (!d || !list) return;
  const sel = document.getElementById('ohbPsBranch'), one = !!(sel && sel.value) || d.scoped;
  const cur = d.current, prv = d.previous;
  chips.innerHTML = `${_bmcChip(`${d.weeks} สัปดาห์ล่าสุด ${fmtDay(cur.from)} ถึง ${fmtDay(cur.to)}`, 'var(--navy-ink)')}
    ${prv ? _bmcChip(`เทียบ ${fmtDay(prv.from)} ถึง ${fmtDay(prv.to)}`, 'var(--text-muted)') : _bmcChip('ไม่มีช่วงก่อนหน้า (ก่อนเมนูใหม่ 1 ก.ย.)', 'var(--text-muted)')}
    ${_bmcChip(`เกณฑ์ใช้สม่ำเสมอ ≤ ${d.steady_pct}%`, 'var(--text-muted)')}
    ${d.benchmark ? _bmcChip(`${d.benchmark.label}: ${d.benchmark.branches.map(x => x.name).join(' · ')}`, 'var(--navy-ink)') : ''}
    ${d.purchase_benchmark ? _bmcChip(`ยอดซื้อจากไดกิโตะ กรุ๊ปของค่ากลาง ฿${_bmcNum(d.purchase_benchmark.per_100k_value, 0)} ต่อยอดขาย ฿100,000 (${d.purchase_benchmark.n} สาขา)`, 'var(--navy-ink)') : ''}
    ${d.benchmark || d.purchase_benchmark ? _bmcChip(`ช่วงยอมรับ ±${_bmcNum(d.buffer_pct, 0)}% (ชั่วคราว)`, 'var(--amber-ink)') : ''}`;
  const cmp = !!(d.benchmark || d.purchase_benchmark);
  const pvs = v => v == null ? '–' : (v > 0 ? '+' : '') + _bmcNum(v, 1) + '%';
  const psrc = { price_file: 'ไฟล์ราคารายสาขา', orders: 'ใบสั่งซื้อที่ส่งจากไดกิโตะ กรุ๊ป' };
  if (!d.branches.length) { box.style.display = 'none'; list.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มียอดขายในช่วงนี้</span>'; return; }
  // the centre without a branch chosen: one line per branch (pick one to open its items), then the chain's items
  if (!one) {
    box.style.display = '';
    box.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">เปรียบเทียบรายสาขา</div>
      <div class="overflow-x-auto"><table class="text-xs w-full"><thead><tr class="text-left border-b" style="color:var(--text-muted)">
        <th class="pr-2">สาขา</th><th class="pr-2 text-right">ยอดขาย (฿)</th><th class="pr-2 text-right">มูลค่าวัตถุดิบต่อยอดขาย ฿100,000 (฿)</th>${cmp ? '<th class="pr-2 text-right">การใช้ตามสูตรเทียบค่ากลาง</th><th class="pr-2 text-right">ยอดซื้อเทียบค่ากลาง</th>' : ''}<th class="pr-2 text-right">รายการที่ใช้ไม่สม่ำเสมอ</th></tr></thead>
      <tbody>${d.branches.map(b => `<tr class="border-b" style="border-color:var(--border-soft)"><td class="pr-2 py-1"><a href="#" onclick="ohbPsPick(${b.branch_id});return false" style="color:var(--accent);text-decoration:underline">${esc(b.branch)}</a>${b.is_benchmark ? ' ' + _bmcChip('ค่ากลาง', 'var(--navy-ink)') : ''}${b.below_benchmark ? ' ' + _bmcChip(b.below_benchmark_th, 'var(--amber-ink)') : ''}${b.no_purchase_th ? ` <span class="text-[10.5px]" style="color:var(--text-muted)">${esc(b.no_purchase_th)}</span>` : ''}</td>
        <td class="pr-2 text-right">${_bmcNum(b.sales, 0)}</td><td class="pr-2 text-right font-semibold">${_bmcNum(b.value_per_100k, 0)}</td>${cmp ? `<td class="pr-2 text-right" style="color:var(--text-secondary)">${pvs(b.vs_benchmark_pct)}</td><td class="pr-2 text-right" style="color:${b.below_benchmark ? 'var(--amber-ink)' : 'var(--text-secondary)'}" title="${b.purchase_per_100k_value == null ? '' : 'ยอดซื้อ ฿' + _bmcNum(b.purchase_per_100k_value, 0) + ' ต่อยอดขาย ฿100,000 · ที่มา ' + (psrc[b.purchase_source] || '') + ' · ' + b.purchase_days + ' วันขาย'}">${pvs(b.purchase_vs_benchmark_pct)}</td>` : ''}
        <td class="pr-2 text-right" style="color:${b.items.some(i => i.steady === false) ? 'var(--amber-ink)' : 'var(--text-muted)'}">${b.items.filter(i => i.steady === false).length}</td></tr>`).join('')}
        ${d.benchmark ? `<tr style="border-top:1px solid var(--border)"><td class="pr-2 py-1 font-semibold">${esc(d.benchmark.label)} <span class="text-[10.5px]" style="color:var(--text-muted)">(มัธยฐานของ ${d.benchmark.n} สาขา)</span></td><td class="pr-2 text-right font-semibold">${_bmcNum(d.benchmark.sales, 0)}</td><td class="pr-2 text-right font-semibold">${_bmcNum(d.benchmark.value_per_100k, 0)}</td><td class="pr-2 text-right">–</td><td class="pr-2 text-right" style="color:var(--text-secondary)">${d.purchase_benchmark ? 'ซื้อ ฿' + _bmcNum(d.purchase_benchmark.per_100k_value, 0) : '–'}</td><td></td></tr>` : ''}
        ${d.chain ? `<tr style="border-top:1px solid var(--border)"><td class="pr-2 py-1 font-semibold">ทั้งเครือ <span class="text-[10.5px]" style="color:var(--text-muted)">(ทุกสาขารวมกัน)</span></td><td class="pr-2 text-right font-semibold">${_bmcNum(d.chain.sales, 0)}</td><td class="pr-2 text-right font-semibold">${_bmcNum(d.chain.value_per_100k, 0)}</td>${cmp ? '<td></td><td></td>' : ''}<td></td></tr>` : ''}</tbody></table></div>
      ${cmp ? `<div class="text-[11px] mt-2" style="color:var(--text-muted)">ค่ากลางคือมัธยฐานของสาขาของบริษัท ใช้เป็นเกณฑ์เทียบ · “ยอดซื้อเทียบค่ากลาง” เทียบมูลค่าที่แต่ละสาขาซื้อจากไดกิโตะ กรุ๊ปต่อยอดขาย ฿100,000 (ไฟล์ราคารายสาขา ถ้าไม่มีใช้ใบสั่งซื้อที่ส่งจากไดกิโตะ กรุ๊ป) · สาขาที่ไม่อยู่ในค่ากลางและซื้อต่ำกว่าค่ากลางเกิน ${_bmcNum(d.buffer_pct, 0)}% มีป้ายเตือน · สาขาที่ไม่มียอดซื้อไม่มีป้าย · ${esc(d.buffer_note || '')}</div>` : ''}`;
    const useBench = !!d.benchmark && _ohbPsView === 'bench';
    const src = useBench ? d.benchmark : d.chain;
    const tog = d.benchmark && d.chain ? ` <a href="#" onclick="_ohbPsView=_ohbPsView==='bench'?'chain':'bench';ohbRenderPerSales();return false" class="text-xs font-normal" style="color:var(--accent)">${useBench ? 'ดูผลรวมทุกสาขา' : 'ดูค่ากลางสาขาของบริษัท'}</a>` : '';
    list.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">${useBench ? esc(d.benchmark.label) + ' · มัธยฐานของสาขาที่ใช้เป็นค่ากลาง' : 'ทั้งเครือ · ปริมาณรวมทุกสาขา ÷ ยอดขายรวมทุกสาขา'}${tog}</div>${src ? _ohbPsTable(src.items.map(i => Object.assign({}, i, { swing_pct: undefined })), ' ') : '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มียอดขายในช่วงนี้</span>'}`;
    return;
  }
  box.style.display = 'none';
  const b = d.branches[0];
  list.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">${esc(b.branch)} · ยอดขาย ฿${_bmcNum(b.sales, 0)} (${b.days} วันขาย) · มูลค่าวัตถุดิบต่อยอดขาย ฿100,000 ≈ ฿${_bmcNum(b.value_per_100k, 0)}
    ${b.is_benchmark ? _bmcChip('สาขาที่ใช้เป็นค่ากลาง', 'var(--navy-ink)') : ''}${b.below_benchmark ? _bmcChip(b.below_benchmark_th, 'var(--amber-ink)') : ''}
    ${b.vs_benchmark_pct != null ? `<span class="text-xs font-normal" style="color:var(--text-secondary)"> · การใช้ตามสูตรเทียบค่ากลาง ${pvs(b.vs_benchmark_pct)}</span>` : ''}
    ${b.purchase_per_100k_value != null ? `<span class="text-xs font-normal" style="color:var(--text-secondary)"> · ซื้อจากไดกิโตะ กรุ๊ป ฿${_bmcNum(b.purchase_per_100k_value, 0)} ต่อยอดขาย ฿100,000${b.purchase_vs_benchmark_pct != null ? ' (ยอดซื้อเทียบค่ากลาง ' + pvs(b.purchase_vs_benchmark_pct) + ')' : ''} · ที่มา ${psrc[b.purchase_source] || ''} ${b.purchase_days} วันขาย</span>` : (b.no_purchase_th ? `<span class="text-xs font-normal" style="color:var(--text-muted)"> · ${esc(b.no_purchase_th)}</span>` : '')}</div>${_ohbPsTable(b.items, undefined, !!d.benchmark)}`;
}
function ohbPsPick(id) { const sel = document.getElementById('ohbPsBranch'); if (sel) { sel.value = String(id); ohbLoadPerSales(); } }
async function ohbLoadPerSales() {
  const sel = document.getElementById('ohbPsBranch'), list = document.getElementById('ohbPsList');
  if (!list) return;
  try {
    if (sel && !sel.options.length) {                         // no picker for a pinned login (it never sees this pane)
      const me = await _bmcJson(`${API}/api/bom-center/stock/me`);
      sel.innerHTML = supplyBranchOptions(me.branches || [], 'ทั้งเครือ (เปรียบเทียบรายสาขา)');
    }
    list.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังคำนวณ…</span>';
    _ohbPs = await _bmcJson(`${API}/api/bom-center/per-sales?${_ohbPsQS()}`);
    ohbRenderPerSales();
  } catch (e) { list.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function ohbPsXlsx() { bmcDownload(`${API}/api/bom-center/per-sales.xlsx?${_ohbPsQS()}`, 'วัตถุดิบต่อยอดขาย 100000.xlsx', 'ohbPsStatus'); }

// ── ใบตัดเบิกสาขา (PR-J4): the branches' daily slips as a log; Sales Support reopens one with a reason ───────
// The list comes from the lateness log (issue-reports/log): per branch and day, how the slip was handled. A row
// with a slip opens its detail (lines, history, export, reopen). Accounting reads the same and downloads the file.
let _ohbIsSt = 'all';
let _ohbIs = null;            // /api/bom-center/issue-reports/log
let _ohbIsSlip = null;        // the slip in the detail panel
const _OHB_IS_ST = [['all', 'ทั้งหมด'], ['on_time', 'ยืนยันตรงวัน'], ['late', 'ล่าช้า'], ['draft', 'ยังไม่ยืนยัน'], ['missing', 'ไม่มีใบ']];
const _OHB_IS_INK = { on_time: 'var(--good-ink)', late: 'var(--amber-ink)', draft: 'var(--amber-ink)', missing: 'var(--bad-ink)', closed: 'var(--text-muted)' };
const _OHB_IS_ACT = { create: 'สร้างใบ', save: 'บันทึกหลังเปิดแก้ไข', confirm: 'ยืนยัน', reconfirm: 'ยืนยันซ้ำ', reopen: 'เปิดแก้ไข', append: 'เบิกเพิ่ม' };
function _ohbIsCanReopen() { return hasModule('supply.ordering') || hasModule('bom.center'); }
function _ohbIsDt(ts) {       // a stored UTC stamp as the branch's clock (Bangkok): d/m HH:MM
  if (!ts) return '';
  const d = new Date(ts);
  return isNaN(d) ? '' : d.toLocaleString('th-TH', { day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Bangkok' });
}
function ohbIsSetStatus(s) { _ohbIsSt = s; ohbIsRender(); }
async function ohbLoadIssues() {
  const sel = document.getElementById('ohbIsBranch');
  if (!sel) return;                         // no picker for a pinned login (centre page)
  const set = (id, v) => { const el = document.getElementById(id); if (el && !el.value) el.value = v; };
  const t = new Date();
  set('ohbIsFrom', _ohbIso(new Date(t.getTime() - 6 * 864e5)));
  set('ohbIsTo', _ohbIso(t));
  const month = _ohbMonthVal();
  const dl = document.getElementById('ohbIsDlMonth'); if (dl) dl.textContent = ' ' + fmtMonthTh(month);
  document.getElementById('ohbIsDetail').style.display = 'none';
  const list = document.getElementById('ohbIsList');
  list.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    // PR-M5: the branch list holds the branches that have data in the month of the control or in the months the range touches
    const months = [...new Set([month, document.getElementById('ohbIsFrom').value.slice(0, 7), document.getElementById('ohbIsTo').value.slice(0, 7)].filter(Boolean))];
    const lists = await Promise.all(months.map(supplyBranchesWithData));
    const byId = new Map();
    lists.flat().forEach(b => byId.set(b.branch_id, b));
    const rows = [...byId.values()].sort((a, b) => String(a.name).localeCompare(String(b.name), 'th'));
    const cur = sel.value;
    sel.innerHTML = supplyBranchOptions(rows, 'ทุกสาขา');
    if (rows.some(b => String(b.branch_id) === cur)) sel.value = cur;
    const q = `date_from=${document.getElementById('ohbIsFrom').value}&date_to=${document.getElementById('ohbIsTo').value}${sel.value ? '&branch_id=' + sel.value : ''}`;
    _ohbIs = await _bmcJson(`${API}/api/bom-center/issue-reports/log?${q}`);
    ohbIsRender();
  } catch (e) { list.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function ohbIsSummaryTable(rows) {
  if (!rows.length) return '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีสาขาที่เริ่มทำใบตัดเบิกในช่วงนี้</span>';
  const th = (s, r) => `<th class="py-1 pr-2${r ? ' text-right' : ''}">${s}</th>`;
  const pct = v => v == null ? '<span style="color:var(--text-muted)">—</span>' : `${_bmcNum(v, 1)}%`;
  const n = (v, ink) => v ? `<b style="color:${ink}">${v}</b>` : '<span style="color:var(--text-muted)">0</span>';
  return `<div style="overflow-x:auto"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
      ${th('สาขา')}${th('วันขาย', 1)}${th('ยืนยันตรงวัน', 1)}${th('ล่าช้า', 1)}${th('ยังไม่ยืนยัน', 1)}${th('ไม่มีใบ', 1)}${th('ตรงวัน %', 1)}${th('มีใบ %', 1)}${th('เปิดแก้ไข', 1)}${th('ยืนยันซ้ำ', 1)}${th('ใบสั่งซื้อล่าช้า', 1)}${th('เคลม', 1)}</tr></thead><tbody>` +
    rows.map(s => `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2 font-semibold">${esc(s.branch || '')}</td>
      <td class="py-1 pr-2 text-right">${s.trading_days}</td><td class="py-1 pr-2 text-right">${n(s.on_time, 'var(--good-ink)')}</td><td class="py-1 pr-2 text-right">${n(s.late, 'var(--amber-ink)')}</td>
      <td class="py-1 pr-2 text-right">${n(s.draft, 'var(--amber-ink)')}</td><td class="py-1 pr-2 text-right">${n(s.missing, 'var(--bad-ink)')}</td>
      <td class="py-1 pr-2 text-right">${pct(s.on_time_pct)}</td><td class="py-1 pr-2 text-right">${pct(s.coverage_pct)}</td>
      <td class="py-1 pr-2 text-right">${s.reopens || 0}</td><td class="py-1 pr-2 text-right">${s.reconfirms || 0}</td>
      <td class="py-1 pr-2 text-right">${n(s.late_orders, 'var(--amber-ink)')}</td><td class="py-1 pr-2 text-right">${s.claims || 0}</td></tr>`).join('') +
    `</tbody></table></div><div class="text-[11px] mt-2" style="color:var(--text-muted)">ตรงวัน % = ยืนยันตรงวัน ÷ ใบที่ยืนยัน · มีใบ % = ใบที่ยืนยัน ÷ วันขาย</div>`;
}
function ohbIsRender() {
  const d = _ohbIs;
  if (!d) return;
  const count = k => k === 'all' ? d.rows.length : d.rows.filter(r => r.status === k).length;
  document.getElementById('ohbIsChips').innerHTML = _OHB_IS_ST.map(([k, t]) => {
    const on = k === _ohbIsSt;
    return `<button onclick="ohbIsSetStatus('${k}')" style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:5px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">${t} ${count(k)}</button>`;
  }).join('');
  const rows = d.rows.filter(r => _ohbIsSt === 'all' || r.status === _ohbIsSt);
  const box = document.getElementById('ohbIsList');
  if (!rows.length) { box.innerHTML = '<span class="text-xs" style="color:var(--good-ink)">✓ ไม่มีรายการในสถานะนี้ในช่วงวันที่เลือก</span>'; }
  else {
    let day = null, html = '';
    rows.forEach(r => {
      if (r.date !== day) { day = r.date; html += `<div class="text-xs font-semibold" style="color:var(--text-primary);margin-top:10px">วัน${esc(r.weekday_th)}ที่ ${esc(fmtDay(r.date))}</div>`; }
      const chips = [r.reopens ? _bmcChip(`เปิดแก้ไข ${r.reopens} ครั้ง`, 'var(--navy-ink)') : '', r.reconfirms ? _bmcChip(`ยืนยันซ้ำ ${r.reconfirms} ครั้ง`, 'var(--text-muted)') : '',
        r.late_orders ? _bmcChip(`ใบสั่งซื้อส่งล่าช้า ${r.late_orders} ใบ`, 'var(--amber-ink)') : '', r.claims ? _bmcChip(`เคลม ${r.claims} เรื่อง`, 'var(--text-muted)') : ''].filter(Boolean).join(' ');
      const conf = r.confirmed_at ? `ยืนยัน ${esc(_ohbIsDt(r.confirmed_at))} โดย ${esc(whoTh(r.confirmed_by))}` : esc(r.status_note || '');
      html += `<div style="border-top:1px solid var(--border);padding:5px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:baseline" class="text-xs">
          <b style="min-width:120px">${esc(r.branch || '')}</b>${_bmcChip(r.status_th, _OHB_IS_INK[r.status] || 'var(--text-muted)')}
          <span style="color:var(--text-secondary)">${conf}</span>${chips}
          ${r.slip_id ? `<a href="#" onclick="ohbIsOpen(${r.slip_id});return false" style="color:var(--accent);margin-left:auto">ดูใบตัดเบิก${r.lines ? ` (${r.lines} รายการ)` : ''}</a>` : ''}</div>`;
    });
    box.innerHTML = html;
  }
  document.getElementById('ohbIsSummary').innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">สรุปต่อสาขา ${esc(_isrDm(d.from))}–${esc(_isrDm(d.to))}</div>${ohbIsSummaryTable(d.summary)}`;
}
async function ohbIsOpen(id) {
  const box = document.getElementById('ohbIsDetail');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    _ohbIsSlip = await _bmcJson(`${API}/api/bom-center/issues/${id}`);
    ohbIsRenderSlip();
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function ohbIsRenderSlip() {
  const s = _ohbIsSlip;
  const key = s.status_key, conf = key === 'confirmed' || key === 'late';
  const ink = { confirmed: 'var(--good-ink)', late: 'var(--amber-ink)', draft: 'var(--amber-ink)' }[key] || 'var(--text-muted)';
  const q = (v, u) => v ? `${_bmcNum(v, 3)} ${esc(u || '')}` : '<span style="color:var(--text-muted)">—</span>';
  const field = 'border:1px solid var(--border-control);border-radius:6px;background:var(--bg-input);color:var(--text-primary)';
  document.getElementById('ohbIsDetail').innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">ใบตัดเบิก ${esc(s.branch || '')} · วันที่ ${esc(fmtDay(s.date))} ${_bmcChip(s.status_th, ink)}${s.appends ? ' ' + _bmcChip(`เบิกเพิ่ม ${s.appends} ครั้ง`, 'var(--navy-ink)') : ''}</div>
      <div>${conf ? `<a href="${API}/api/bom-center/issues/export/${s.slip_id}.xlsx" style="color:var(--accent);font-size:12px;margin-right:12px">ดาวน์โหลดใบนี้ (Excel)</a>` : ''}${_ohbBtn('ปิด', "document.getElementById('ohbIsDetail').style.display='none'")}</div></div>
    <div class="text-xs mt-1" style="color:var(--text-secondary)">${conf ? `ยืนยันเมื่อ ${esc(_ohbIsDt(s.confirmed_at))} โดย ${esc(whoTh(s.confirmed_by))}` : 'ยังไม่ยืนยัน'}${s.reopened_at ? ` · เปิดแก้ไขล่าสุดเมื่อ ${esc(_ohbIsDt(s.reopened_at))} โดย ${esc(whoTh(s.reopened_by))}` : ''}</div>
    <div id="ohbIsResult" class="text-xs mt-1"></div>
    <div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
      <th class="py-1 pr-2">วัตถุดิบ</th><th class="py-1 pr-2 text-right">เบิกใช้</th><th class="py-1 pr-2 text-right">ของเสีย</th><th class="py-1">สาเหตุของเสีย</th></tr></thead><tbody>
      ${s.lines.map(l => `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(l.name || l.code)}<div class="text-[10.5px]" style="color:var(--text-muted)">${esc(l.code)}</div></td>
        <td class="py-1 pr-2 text-right">${l.qty_used ? _bmcNum(l.qty_used, 3) + ' ' + esc(l.unit || '') : '<span style="color:var(--text-muted)">—</span>'}</td>
        <td class="py-1 pr-2 text-right">${l.qty_waste ? _bmcNum(l.qty_waste, 3) + ' ' + esc(l.unit || '') : '<span style="color:var(--text-muted)">—</span>'}</td>
        <td class="py-1">${l.waste_reason_th ? esc(l.waste_reason_th) + (l.note ? ' · ' + esc(l.note) : '') : ''}</td></tr>`).join('') || '<tr><td colspan="4" class="py-2" style="color:var(--text-muted)">ไม่มีรายการ</td></tr>'}</tbody></table></div>
    <div class="text-xs font-semibold mt-3" style="color:var(--text-primary)">ประวัติของใบ</div>
    ${s.log.map(h => `<div class="text-xs" style="border-top:1px solid var(--border);padding:3px 0;color:var(--text-secondary)">${esc(_ohbIsDt(h.at))} · ${esc(_OHB_IS_ACT[h.action] || h.action)} · ${esc(whoTh(h.who))}${h.action === 'confirm' && h.late ? ' · ' + _bmcChip('ยืนยันล่าช้า', 'var(--amber-ink)') : ''}${h.lines != null ? ` · ${h.lines} รายการ` : ''}${h.note ? (h.action === 'append' ? ' · ' : ' · เหตุผล: ') + esc(h.note) : ''}</div>`).join('')}
    ${conf && _ohbIsCanReopen() ? `<textarea id="ohbIsReopenNote" rows="2" placeholder="เหตุผลที่เปิดแก้ไข (จำเป็น)" style="width:100%;margin-top:10px;padding:8px;font-size:13px;${field}"></textarea>
      <div class="flex flex-wrap items-center gap-2 mt-2">${_ohbBtn('เปิดให้สาขาแก้ไข', 'ohbIsReopen()', true)}<span id="ohbIsReopenStatus" class="text-xs"></span></div>
      <div class="text-[11px] mt-1" style="color:var(--text-muted)">ใบกลับเป็นร่างจนสาขายืนยันใหม่ · งวดที่บัญชีบันทึกแล้วเปิดแก้ไขไม่ได้</div>`
      : (conf ? '<div class="text-[11px] mt-2" style="color:var(--text-muted)">ประสานงานขายเป็นผู้เปิดแก้ไขใบที่ยืนยันแล้ว</div>'
        : (key === 'draft' && _ohbIsCanReopen() ? `<div class="flex flex-wrap items-center gap-2 mt-3"><button disabled aria-disabled="true" id="ohbIsReopenOff" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:7px 14px;font-size:13px;background:transparent;color:var(--text-muted);opacity:.7;cursor:not-allowed">เปิดให้สาขาแก้ไข</button>
          <span class="text-xs" style="color:var(--text-secondary)">ใบนี้ยังเป็นร่าง สาขาแก้ไขได้เอง</span></div>` : ''))}`;
}
async function ohbIsReopen() {
  const s = _ohbIsSlip;
  if (!s) return;
  const note = (document.getElementById('ohbIsReopenNote').value || '').trim();
  if (!note) { _bmcStatus('ohbIsReopenStatus', 'ระบุเหตุผลที่เปิดแก้ไข', 'var(--amber-ink)'); return; }
  if (!await uiConfirm(`เปิดใบตัดเบิก ${s.branch || ''} วันที่ ${fmtDay(s.date)} ให้สาขาแก้ไข?\nใบกลับเป็นร่างจนสาขายืนยันใหม่`, { title: 'ยืนยันการเปิดแก้ไขใบตัดเบิก', ok: 'เปิดแก้ไข', cancel: 'ไม่เปิด' })) return;
  try {
    await _bmcJson(`${API}/api/bom-center/issues/reopen`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slip_id: s.slip_id, note }) });
    await ohbIsOpen(s.slip_id);
    _bmcStatus('ohbIsResult', '✓ เปิดแก้ไขแล้ว · สาขาแก้ไขและยืนยันใหม่ได้', 'var(--good-ink)');
    ohbLoadIssues();
  } catch (e) { _bmcStatus('ohbIsReopenStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}
// a download that reports the server's reason (no confirmed slip in that month …) instead of opening a JSON page
function _ohbIsDownload(url, name, statusId) { return bmcDownload(url, name, statusId || 'ohbIsStatus'); }
function ohbIsXlsx() {
  const m = _ohbMonthVal(), b = document.getElementById('ohbIsBranch').value;
  _ohbIsDownload(`${API}/api/bom-center/issues/export.xlsx?month=${m}${b ? '&branch_id=' + b : ''}`, `bom_center_issue_slips_${m}.xlsx`);
}
function ohbIsLogXlsx() {
  const m = _ohbMonthVal(), b = document.getElementById('ohbIsBranch').value;
  _ohbIsDownload(`${API}/api/bom-center/issue-reports/log.xlsx?month=${m}${b ? '&branch_id=' + b : ''}`, `bom_center_issue_log_${m}.xlsx`);
}

// ── เคลมสินค้า (owner 2026-10-08, 2026-10-09): Sales Support decides; the photos arrive by LINE ───────────
// A claim raised while receiving (origin 'receive') leaves the stock as the branch received it: approving changes
// nothing, and the part not approved is added back as a receipt. A later claim ('after'), once approved, takes the
// approved quantity out of the branch's received goods (a negative goods-receipt dated the day the order arrived),
// so the usage variance stays true.
let _ohbClaimSt = 'submitted';
let _ohbClaimOpen = null;     // the claim in the detail panel
const _OHB_CLAIM_ST = [['submitted', 'รอพิจารณา'], ['approved', 'อนุมัติแล้ว'], ['rejected', 'ไม่อนุมัติ'], ['all', 'ทั้งหมด']];
const _OHB_CLAIM_INK = { submitted: 'var(--amber-ink)', approved: 'var(--good-ink)', rejected: 'var(--bad-ink)', cancelled: 'var(--text-muted)' };
function _ohbCanDecide() { return hasModule('supply.ordering'); }
function ohbClaimSetStatus(s) { _ohbClaimSt = s; ohbLoadClaims(); }
async function ohbLoadClaims() {
  document.getElementById('ohbClaimChips').innerHTML = _OHB_CLAIM_ST.map(([k, t]) => {
    const on = k === _ohbClaimSt;
    return `<button onclick="ohbClaimSetStatus('${k}')" style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:5px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">${t}</button>`;
  }).join('');
  const box = document.getElementById('ohbClaimList');
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/claims?status=${_ohbClaimSt}&limit=100`);
    // PR-M5: two lines — "สาขา · รอบ ส่ง d/m · N รายการ", then where it was raised and what became of it (no ids, no login)
    box.innerHTML = d.claims.length ? d.claims.map(c => `<a href="#" class="ohb-claimrow" data-claim="${c.claim_id}" onclick="ohbClaimView(${c.claim_id});return false">
        <div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(c.branch || '')} · ${esc(_OHB_RND[c.round] || c.round || '')} ส่ง ${esc(_isrDm(c.delivery || c.cycle_date || ''))} · ${c.lines} รายการ</div>
        <div class="mt-0.5">${_bmcChip(c.origin_th || '', c.origin === 'receive' ? 'var(--navy-ink)' : 'var(--text-muted)')}
          ${_bmcChip(c.status_th, _OHB_CLAIM_INK[c.status] || 'var(--text-muted)')}</div></a>`).join('')
      : '<span class="text-xs" style="color:var(--good-ink)">✓ ไม่มีเคลมในสถานะนี้</span>';
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function ohbClaimView(id) {
  const box = document.getElementById('ohbClaimDetail');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    _ohbClaimOpen = await _bmcJson(`${API}/api/bom-center/claims/${id}`);
    ohbClaimRender();
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function ohbClaimRender() {
  const c = _ohbClaimOpen;
  const act = _ohbCanDecide() && c.status === 'submitted';
  const field = 'border:1px solid var(--border-control);border-radius:6px;background:var(--bg-input);color:var(--text-primary)';
  document.getElementById('ohbClaimDetail').innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">เคลม #${c.claim_id} · ${esc(c.branch || '')} · ใบสั่งซื้อ #${c.order_id} · สั่ง ${esc(fmtDay(c.cycle_date || ''))}</div>
      ${_ohbBtn('ปิด', "document.getElementById('ohbClaimDetail').style.display='none'")}</div>
    <div class="text-xs mt-1" style="color:var(--text-secondary)">${_bmcChip(c.origin_th || '', c.origin === 'receive' ? 'var(--navy-ink)' : 'var(--text-muted)')} ${_bmcChip(c.status_th, _OHB_CLAIM_INK[c.status] || 'var(--text-muted)')}
      แจ้งโดย ${esc(whoTh(c.created_by))} เมื่อ ${esc(fmtAt(c.created_at))}${c.note ? ' · ' + esc(c.note) : ''}
      ${c.decided_by ? ` · พิจารณาโดย ${esc(whoTh(c.decided_by))}${c.decision_note ? ': ' + esc(c.decision_note) : ''}` : ''}</div>
    ${c.photos.length ? `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px">${c.photos.map((p, i) => `<img src="${API}/api/bom-center/claims/${c.claim_id}/photo/${p.photo_id}" alt="รูปเคลมที่ ${i + 1}" loading="lazy" onclick="brnClaimLight(this.src)" style="width:120px;height:120px;object-fit:cover;border-radius:8px;border:1px solid var(--border);cursor:zoom-in">`).join('')}</div>
      <div class="text-[11px] mt-1" style="color:var(--text-muted)">กดที่รูปเพื่อดูภาพขยาย</div>`
      : '<div class="text-xs mt-2" style="color:var(--text-muted)">รูปถ่ายส่งทางไลน์</div>'}
    <div id="ohbClaimResult" class="text-xs mt-1"></div>
    <div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
      <th class="py-1 pr-2">วัตถุดิบ</th><th class="py-1 pr-2 text-right">ส่งจริง</th><th class="py-1 pr-2 text-right">รับแล้ว</th><th class="py-1 pr-2 text-right">สาขาเคลม</th><th class="py-1 pr-2">สาเหตุ</th><th class="py-1 text-right">${act ? 'อนุมัติ' : 'อนุมัติแล้ว'}</th></tr></thead><tbody>
      ${c.lines.map(l => `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(l.name || l.code)}<div class="text-[10.5px]" style="color:var(--text-muted)">${esc(l.code)}</div></td>
        <td class="py-1 pr-2 text-right">${_bmcNum(l.sent_units)}</td><td class="py-1 pr-2 text-right">${_bmcNum(l.received_units)}</td>
        <td class="py-1 pr-2 text-right"><b>${_bmcNum(l.claim_units)}</b> ${esc(l.unit || '')}</td>
        <td class="py-1 pr-2">${esc(l.reason_th)}${l.note ? '<div class="text-[10.5px]" style="color:var(--text-muted)">' + esc(l.note) + '</div>' : ''}</td>
        <td class="py-1 text-right">${act ? `<input inputmode="decimal" id="ohbCA_${esc(l.code)}" value="${esc(String(l.claim_units))}" style="width:70px;text-align:center;padding:3px;${field}">` : (l.approved_units == null ? '—' : _bmcNum(l.approved_units))}</td></tr>`).join('')}</tbody></table></div>
    <div id="ohbClaimEffect" class="text-xs mt-2" style="color:var(--text-secondary)">${c.origin === 'receive'
      ? 'เคลมนี้แจ้งขณะรับสินค้า สาขารับเข้าตามจำนวนจริงแล้ว จึงอนุมัติแล้วสต็อกไม่เปลี่ยน ส่วนที่ไม่อนุมัติบันทึกเป็นรับเข้า'
      : 'เคลมนี้แจ้งภายหลัง อนุมัติแล้วหักจำนวนที่อนุมัติออกจากสินค้าที่สาขารับ ส่วนที่ไม่อนุมัติสต็อกไม่เปลี่ยน'}</div>
    ${act ? `<textarea id="ohbClaimNote" rows="2" placeholder="หมายเหตุถึงสาขา (จำเป็นเมื่อไม่อนุมัติ)" style="width:100%;margin-top:8px;padding:8px;font-size:13px;${field}"></textarea>
      <div class="flex flex-wrap items-center gap-2 mt-2">
        ${_ohbBtn('อนุมัติ', "ohbClaimDecide('approve')", true)}${_ohbBtn('ไม่อนุมัติ', "ohbClaimDecide('reject')")}<span id="ohbClaimStatus" class="text-xs"></span></div>`
      : (c.status === 'submitted' ? '<div class="text-xs mt-2" style="color:var(--text-muted)">ประสานงานขายเป็นผู้พิจารณาเคลม</div>' : '')}`;
}
async function ohbClaimDecide(decision) {
  const c = _ohbClaimOpen;
  if (!c) return;
  const note = (document.getElementById('ohbClaimNote').value || '').trim();
  const lines = c.lines.map(l => { const el = document.getElementById('ohbCA_' + l.code);
    return { code: l.code, approved_units: el && el.value !== '' ? Number(String(el.value).replace(',', '.')) : l.claim_units }; });
  if (decision === 'reject' && !note) { _bmcStatus('ohbClaimStatus', 'ระบุเหตุผลที่ไม่อนุมัติ', 'var(--amber-ink)'); return; }
  const n = lines.filter(l => l.approved_units > 0).length;
  const recv = c.origin === 'receive';
  const partial = c.lines.some((l, i) => lines[i].approved_units < l.claim_units - 1e-9);
  const ask = decision === 'approve'
    ? (recv ? `อนุมัติเคลม #${c.claim_id} · ${n} รายการ?\nรับเข้าตามจริงอยู่แล้ว`
        + (partial ? '\nส่วนที่ไม่อนุมัติบันทึกเป็นรับเข้า' : '')
      : `อนุมัติเคลม #${c.claim_id} · ${n} รายการ?\nหักออกจากรับเข้าของสาขา${c.branch || ''}`)
    : `ไม่อนุมัติเคลม #${c.claim_id}?\nสาขาจะเห็นเหตุผลที่ระบุ` + (recv ? '\nบันทึกจำนวนที่เคลมเป็นรับเข้า' : '');
  if (!await uiConfirm(ask, decision === 'approve'
    ? { title: 'ยืนยันการอนุมัติเคลม', ok: 'อนุมัติ', cancel: 'ยกเลิก' }
    : { title: 'ยืนยันการไม่อนุมัติเคลม', ok: 'ไม่อนุมัติ', cancel: 'ยกเลิก' })) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/claim-act/decide`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ claim_id: c.claim_id, decision, lines, note: note || null }) });
    await ohbClaimView(c.claim_id);
    _bmcStatus('ohbClaimResult', decision === 'approve'
      ? '✓ อนุมัติแล้ว' + (r.adjusted ? ` · ปรับสินค้าของสาขา ${r.adjusted} รายการ (ลงวันที่ ${fmtDay(r.receipt_date)})` : '')
        + (r.restored ? ` · บันทึกส่วนที่ไม่อนุมัติเป็นรับเข้า ${r.restored} รายการ` : '')
      : '✓ บันทึกการไม่อนุมัติแล้ว' + (r.restored ? ` · บันทึกเป็นรับเข้า ${r.restored} รายการ` : ''), 'var(--good-ink)');
    ohbLoadClaims();
  } catch (e) { _bmcStatus('ohbClaimStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}
