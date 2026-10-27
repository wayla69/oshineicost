// 19-fmsg.js — ข้อความจากเจ้าของสาขา (PR-F4, owner 2026-10-22): head office's inbox of the franchise owners' questions (the tab fmsg,
// tabs/59-fmsg.html; management + operation, module franchise.inbox). GET /api/franchise/messages/inbox and GET/POST
// /api/franchise/messages/{id}[/reply|/status] (backend/routers/franchise_msgs.py). Draws with the pure helpers of js/17-franchise.js
// (_fmFetch, _fmPost, _fmRowHtml, _fmMessagesHtml, _fmComposer, _fmCtxText, _fmWhen, _FM_FILTER): every string a person typed goes through
// esc(). Kept apart from ร้านของฉัน: that page never names a branch, this one filters by branch. Every name starts with `fmsg`.
// ── PR-F4: ข้อความจากเจ้าของสาขา (head office, the tab fmsg) ───────────────────────────────────────────────────────────────
// Every franchise thread: filter by branch and by state (ยังไม่ปิด by default; ปิดแล้ว / ทั้งหมด), unread first; open one -> its messages,
// a reply box, ปิดหัวข้อ / เปิดหัวข้ออีกครั้ง. The owner sees the replier's display name or Thai role label (the server's author field).
/** @type {any} */
let _fmsgList = null;
/** @type {any} */
let _fmsgCur = null;
let _fmsgBranch = '';
let _fmsgGen = 0;
let _fmsgOpenGen = 0;
let _fmsgBusy = false;
function _fmsgStatusKey() { const f = uiStateFilter.get(['todo', 'done', 'all']); return f === 'done' ? 'closed' : f === 'all' ? 'all' : 'open'; }
async function loadFmsgInbox() {
  const gen = ++_fmsgGen;
  const list = document.getElementById('fmsgList');
  const qs = new URLSearchParams({ status: _fmsgStatusKey() });
  if (_fmsgBranch) qs.set('branch_id', _fmsgBranch);
  let d;
  try { d = await _fmFetch(`/api/franchise/messages/inbox?${qs}`); }
  catch (e) { if (gen === _fmsgGen && list) list.innerHTML = `<div class="ms-card"><div class="ms-row" style="color:var(--bad-ink)">${esc(e.message)}</div></div>`; return; }
  if (gen !== _fmsgGen) return;
  _fmsgList = d;
  const sel = document.getElementById('fmsgBranch');
  if (sel) {
    sel.innerHTML = `<option value="">ทุกสาขา</option>` + (d.branches || []).map(b => `<option value="${Number(b.branch_id) || 0}">${esc(b.branch)}${b.unread ? ' (ใหม่ ' + fmtN(b.unread) + ')' : ''}</option>`).join('');
    sel.value = _fmsgBranch;
  }
  const c = d.counts || {};
  uiStateFilter.bar('fmsgState', { counts: { todo: c.open || 0, done: c.closed || 0, all: c.all || 0 }, f: uiStateFilter.get(['todo', 'done', 'all']), pick: 'fmsgFilter', opts: _FM_FILTER });
  const st = document.getElementById('fmsgUnread');
  if (st) st.textContent = d.unread ? `ยังไม่ได้อ่าน ${fmtN(d.unread)} หัวข้อ` : '';
  fmsgRenderList();
  const p = _fmsgPending;
  if (p && p.go === 'fmsg') { _fmsgPending = null; if (p.t) { await fmsgOpen(p.t); return; } }
  if (_fmsgCur) await fmsgOpen(_fmsgCur.id, true); else fmsgRenderThread();
}
function fmsgRenderList() {
  const list = document.getElementById('fmsgList');
  if (!list || !_fmsgList) return;
  const rows = _fmsgList.threads || [];
  list.innerHTML = rows.length ? `<div class="ms-card fm-card">${rows.map(t => _fmRowHtml(t, 'fmsgOpen', true)).join('')}</div>`
    : `<div class="ms-card fm-card">${(_fmsgList.counts || {}).all ? uiStateFilter.empty(uiStateFilter.get(['todo', 'done', 'all']), 'fmsgFilter', 'หัวข้อ') : '<div class="ms-row" style="color:var(--text-muted);font-size:13px">ยังไม่มีข้อความจากเจ้าของสาขา</div>'}</div>`;
  if (_fmsgCur) list.querySelectorAll(`.fm-row[data-tid="${Number(_fmsgCur.id) || 0}"]`).forEach(b => b.classList.add('fm-on'));
}
function fmsgFilter(f) { uiStateFilter.set(f); loadFmsgInbox(); }
function fmsgBranchChanged() { const sel = document.getElementById('fmsgBranch'); _fmsgBranch = sel ? sel.value : ''; _fmsgCur = null; loadFmsgInbox(); }
async function fmsgOpen(tid, quiet) {
  const gen = ++_fmsgOpenGen;                      // audit 2026-10: only the newest open draws (a slow answer for an older click is dropped)
  let t;
  try { t = await _fmFetch(`/api/franchise/messages/${Number(tid) || 0}`); }
  catch (e) { if (gen !== _fmsgOpenGen) return; if (!quiet) uiToast(e.message, 'error'); _fmsgCur = null; fmsgRenderThread(); return; }
  if (gen !== _fmsgOpenGen) return;
  _fmsgCur = t;
  const row = ((_fmsgList && _fmsgList.threads) || []).find(x => x.id === _fmsgCur.id);
  if (row) row.unread = 0;
  fmsgRenderList();
  fmsgRenderThread();
  if (!quiet) {
    const box = document.getElementById('fmsgThread');
    if (box && window.matchMedia && window.matchMedia('(max-width:900px)').matches) { try { box.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { /* old browser */ } }
    try { navBadgesRefresh(true); } catch (e) { /* no launcher slice */ }
  }
}
function fmsgRenderThread() {
  const box = document.getElementById('fmsgThread');
  if (!box) return;
  const t = _fmsgCur;
  if (!t) { _fmForget(box); box.innerHTML = `<div class="ms-card fm-card"><div class="ms-row" style="color:var(--text-muted);font-size:13px">เลือกหัวข้อจากรายการเพื่ออ่านและตอบ</div></div>`; return; }
  const max = t.max_body || 1000;
  const ctx = t.context ? `<div class="fm-ctx"><span class="fm-ctx-t">เรื่องที่อ้างถึง: ${esc(_fmCtxText(t.context))}</span></div>` : '';
  const state = t.status === 'closed'
    ? `<span class="fm-chip">ปิดแล้ว${t.closed_by ? ' · ' + esc(t.closed_by) : ''}${t.closed_at ? ' · ' + esc(_fmWhen(t.closed_at)) : ''}</span>` : '';
  const act = t.status === 'open'
    ? `<button type="button" class="fm-btn" onclick="fmsgSetStatus(${Number(t.id) || 0},'closed')">ปิดหัวข้อ</button>`
    : `<button type="button" class="fm-btn" onclick="fmsgSetStatus(${Number(t.id) || 0},'open')">เปิดหัวข้ออีกครั้ง</button>`;
  const foot = t.status === 'open'
    ? `<label class="fm-label" for="fmsgReply">ตอบกลับ</label>${_fmComposer('fmsgReply', max, 'พิมพ์คำตอบถึงเจ้าของสาขา')}`
      + `<div class="fm-actions"><button type="button" class="fm-btn fm-primary" id="fmsgSend" onclick="fmsgSendReply(${Number(t.id) || 0})">ส่งคำตอบ</button></div>` : '';
  const keep = _fmKeep(box, 'thread:' + (Number(t.id) || 0), ['fmsgReply']);     // audit 2026-10: a reload keeps a half-typed reply
  box.innerHTML = `<div class="ms-card fm-card"><div class="fm-actions"><span class="fm-topic fm-grow">${esc(t.branch || '')} · ${esc(t.topic_th || '')}</span>${state}${act}</div>`
    + ctx + `<div class="fm-thread">${_fmMessagesHtml(t.messages, 'hq')}</div>${foot}</div>`;
  keep();
}
async function fmsgSendReply(tid) {
  if (_fmsgBusy) return;
  const text = String((document.getElementById('fmsgReply') || {}).value || '').trim();
  if (!text) { uiToast('พิมพ์คำตอบ', 'warn'); return; }
  _fmsgBusy = true;
  const btn = document.getElementById('fmsgSend'); if (btn) btn.disabled = true;
  try {
    _fmsgCur = await _fmPost(`/api/franchise/messages/${Number(tid) || 0}/reply`, { body: text });
    _fmsgOpenGen++;                                  // an open still on its way carries the thread without this reply
    uiToast('ส่งคำตอบถึงเจ้าของสาขาแล้ว', 'success');
    _fmForget(document.getElementById('fmsgThread'));     // the reply is sent: the box starts empty
    fmsgRenderThread();
    loadFmsgInbox();
  } catch (e) {
    uiToast(e.message, 'error');
    if (btn) btn.disabled = false;
  } finally { _fmsgBusy = false; }
}
async function fmsgSetStatus(tid, status) {
  try {
    _fmsgCur = await _fmPost(`/api/franchise/messages/${Number(tid) || 0}/status`, { status });
    _fmsgOpenGen++;
    uiToast(status === 'closed' ? 'ปิดหัวข้อแล้ว' : 'เปิดหัวข้ออีกครั้งแล้ว', 'success');
    if (status === 'closed' && _fmsgStatusKey() === 'open') _fmsgCur = null;    // a finished thread leaves the default list
    loadFmsgInbox();
  } catch (e) { uiToast(e.message, 'error'); }
}
