// ─── 09c-mobile.js — The mobile owner summary (ms* views) and the app BOOT: ensureChartSetup(), init(), waitForCharts() ───
// Slice 3/5 of the former 09-deep-tools.js (split 2026-09-05 by contiguous range, so every
// statement keeps its load order). Shared global scope: index.html loads js/*.js in name order.

// ════════════════════════════════════════════════════════════════
// MOBILE OWNER SUMMARY
// ════════════════════════════════════════════════════════════════
async function showFullDashboard(tab) {
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
function showMobileSummary() {
  document.body.classList.remove('force-desktop');
  try { localStorage.setItem('oshinei_phone_mode', 'summary'); } catch (e) {}
  window.scrollTo(0, 0);
  loadMobileSummary(true);
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
        if (String(d.url || '').includes('go=supply')) { if (_desktopBooted) supplyDeepLink(d.url); else _supplyPendingLink = d.url; }   // a supply reminder (batch 3): that page, not the owner's summary
        else { _msPendingLink = d.url || ''; showMobileSummary(); }
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
      if (q.get('go') === 'supply') _supplyPendingLink = location.search; else _msPendingLink = location.search;     // supply reminders (batch 3) open a supply page
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
  if (sub) st.textContent = note || (sup ? 'เปิดอยู่ · เครื่องนี้จะได้รับงานของวันนี้ตอนเช้า และเตือนก่อนตัดรอบ 13:15' : 'เปิดอยู่ · เครื่องนี้จะได้รับสรุปเมื่อวานทุกเช้า 08:30');
  else if (denied) st.textContent = 'ถูกปิดไว้ในการตั้งค่าของเบราว์เซอร์ — เปิดการแจ้งเตือนให้เว็บนี้ก่อน';
  else st.textContent = note || (sup ? 'ยังไม่เปิด · รับงานของวันนี้ตอนเช้า และเตือนก่อนตัดรอบ 13:15 บนเครื่องนี้' : 'ยังไม่เปิด · รับสรุปเมื่อวานทุกเช้า 08:30 บนเครื่องนี้');
  if (on) on.style.display = sub || denied ? 'none' : '';
  if (off) off.style.display = sub ? '' : 'none';
  if (test) test.style.display = sub ? '' : 'none';
  const later = document.getElementById(p + 'Later');
  if (later) { later.textContent = sub ? 'ซ่อน' : 'ไว้ก่อน'; later.setAttribute('onclick', sub ? 'msPushHide()' : 'msPushLater()'); }
  msPushKinds(sub, p);
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
    _msSalesGran = 'month', _msBranchSort = 'rev';
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
const msGlanceLine = html => html ? `<p style="font-size:12px;line-height:1.45;color:var(--text-secondary,#475569);margin:2px 0 8px">${html}</p>` : '';

// A small column chart in SVG (no Chart.js on the phone): oldest→newest, the
// latest complete period in the accent, the period in progress hollow + dashed,
// weekends tinted, a dashed average line, value labels on the latest and the peak.
function msColumns(vals, o) {
  const n = vals.length;
  if (n < 2) return '';
  // o.under (optional): one {t, c} per column printed under its label (the month-by-month view
  // prints the change on the previous month); o.allLabels labels every column, not just the ends
  const under = Array.isArray(o.under) ? o.under : null;
  const W = 320, H = o.height || 100, top = 16, bottom = under ? 31 : 14, plotH = H - top - bottom;
  const P = o.partial == null ? -1 : o.partial;
  const max = Math.max(1, ...vals.filter(v => v != null));
  const step = W / n, bw = Math.max(3, Math.min(22, step * 0.64));
  const yOf = v => top + plotH * (1 - Math.max(0, v) / max);
  const comp = vals.map((v, i) => i).filter(i => vals[i] != null && i !== P);
  const last = comp.length ? comp[comp.length - 1] : -1;
  const hi = comp.reduce((b, i) => (b < 0 || vals[i] > vals[b]) ? i : b, -1);
  const avg = comp.length >= 3 ? comp.reduce((a, i) => a + vals[i], 0) / comp.length : null;
  const cx = i => i * step + step / 2;
  const placed = [];
  const txt = (t, x, y, size, weight, color, check) => {
    const w = t.length * size * 0.56;
    x = Math.max(w / 2 + 1, Math.min(W - w / 2 - 1, x));
    if (check) {
      if (placed.some(p => Math.abs(p.x - x) < (p.w + w) / 2 + 3 && Math.abs(p.y - y) < size + 2)) return '';
      placed.push({ x, y, w });
    }
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" font-size="${size}" font-weight="${weight}" style="fill:${color}">${esc(t)}</text>`;
  };
  let g = '';
  (o.weekend || []).forEach((w, i) => { if (w) g += `<rect x="${(i * step).toFixed(1)}" y="${top - 4}" width="${step.toFixed(1)}" height="${plotH + 4}" style="fill:var(--bg-subtle,#f1f5f9)"/>`; });
  if (avg != null) {
    const ya = yOf(avg).toFixed(1);
    g += `<line x1="0" x2="${W}" y1="${ya}" y2="${ya}" stroke-width="1" stroke-dasharray="3 3" style="stroke:var(--text-muted,#94a3b8)"/>`;
  }
  vals.forEach((v, i) => {
    if (v == null) return;
    const x = (cx(i) - bw / 2).toFixed(1), y = yOf(v), h = Math.max(1, top + plotH - y);
    if (i === P) g += `<rect x="${x}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" stroke-width="1.2" stroke-dasharray="3 2" style="fill:none;stroke:var(--group, var(--accent,#1f3a6b))"/>`;
    else g += `<rect x="${x}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="3" style="fill:var(--group, var(--accent,#1f3a6b));opacity:${i === last ? 1 : 0.38}"/>`;
  });
  const lab = (i, t) => txt(t ?? o.fmt(vals[i]), cx(i), yOf(vals[i]) - 4, 11, 600, 'var(--text-primary,#1e293b)', true);
  // priority: the latest complete value, then the period in progress (just its
  // tag - the figure is in the list/tile), then the peak
  if (last >= 0) g += lab(last);
  if (P >= 0 && vals[P] != null) g += lab(P, o.grain === 'day' ? 'วันนี้' : 'MTD');
  if (hi >= 0 && hi !== last) g += lab(hi);
  if (avg != null) g += `<text x="2" y="${(yOf(avg) - 3).toFixed(1)}" font-size="11" style="fill:var(--text-muted,#94a3b8)">เฉลี่ย ${esc(o.fmt(avg))}</text>`;
  if (o.labels) {
    const ly = under ? H - 17 : H - 2;
    if (o.allLabels) o.labels.forEach((t, i) => { g += txt(t, cx(i), ly, 11, i === last ? 700 : 400, i === last ? 'var(--text-primary,#1e293b)' : 'var(--text-muted,#94a3b8)'); });
    else {
      g += txt(o.labels[0], cx(0), ly, 11, 400, 'var(--text-muted,#94a3b8)');
      g += txt(o.labels[n - 1], cx(n - 1), ly, 11, 400, 'var(--text-muted,#94a3b8)');
    }
  }
  if (under) under.forEach((u, i) => { if (u && u.t) g += txt(u.t, cx(i), H - 3, 11, 600, u.c || 'var(--text-muted,#94a3b8)'); });
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
const _MS_GROUP = { summary: 'home', sales: 'sales', branches: 'buffet', menu: 'menu', profit: 'profit', discounts: 'money' };
const _MS_VIEWS = ['summary', 'sales', 'branches', 'menu', 'profit', 'discounts'];
// a phone reader sees only what their role may open on the desktop (same module names as auth.py)
const _msMod = m => !(window.AUTH && window.AUTH.enabled) || (window.AUTH.modules || []).includes(m);
function msSwitchView(v) {
  _msView = v;
  document.body.dataset.group = _MS_GROUP[v] || 'home';
  _MS_VIEWS.forEach(x => {
    const el = document.getElementById('msv-' + x); if (el) el.style.display = x === v ? '' : 'none';
    document.getElementById('msnav-' + x)?.classList.toggle('active', x === v);
  });
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

// ── entry point: fetch the month payload, then render the active view ──
async function loadMobileSummary(force) {
  const status = document.getElementById('msStatus');
  if (status) status.textContent = 'กำลังโหลด…';
  const navP = document.getElementById('msnav-profit'), navD = document.getElementById('msnav-discounts');
  if (navP) navP.style.display = (_msMod('cost.analysis') || _msMod('profit.pnl')) ? '' : 'none';
  if (navD) navD.style.display = _msMod('ops.discounts') ? '' : 'none';
  const br = msBranch(), mon = msMonth();
  let j;
  try { j = await msFetchRpt(br, mon); }
  catch (e) { if (status) status.textContent = 'โหลดไม่สำเร็จ — ลองรีเฟรช'; return; }
  if (!j || !j.available) { if (status) status.textContent = 'ยังไม่มีข้อมูลการขาย'; return; }
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
  if (status) status.textContent = `เทียบ${j.comp_label || 'เดือนก่อน'} · ข้อมูลถึง ${j.latest_day || ''}` + (_msFetchedAt ? ` · โหลดล่าสุด ${msClock(_msFetchedAt)}` : '');
  msRenderActive();
  msLoadVoice();
  if (_msPendingLink) { const l = _msPendingLink; _msPendingLink = ''; msDeepLink(l); }
}

// ── the customer-voice queue on the phone (E4): what waits for an answer, the
// branch's own when the login is scoped, else the selected branch / the chain ──
async function msLoadVoice() {
  const wrap = document.getElementById('msVoiceWrap'), box = document.getElementById('msVoice');
  if (!wrap || !box) return;
  if (window.AUTH && window.AUTH.enabled && !(window.AUTH.modules || []).includes('customer.feedback')) { wrap.style.display = 'none'; return; }
  const p = new URLSearchParams({ needs_reply: '1', sort: 'priority', limit: '5' });
  const br = (window.AUTH && window.AUTH.branch) || msBranch(); if (br) p.set('branch', br);
  let d;
  try { const r = await fetch(`${API}/api/feedback/items?${p}`); if (!r.ok) { wrap.style.display = 'none'; return; } d = await r.json(); }
  catch (e) { wrap.style.display = 'none'; return; }
  const items = d.items || [];
  wrap.style.display = '';
  document.getElementById('msVoiceCount').textContent = items.length ? `(${items.length}${items.length >= 5 ? '+' : ''})` : '';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  box.innerHTML = items.length ? items.map(r => `<div style="padding:8px 0;border-bottom:1px solid var(--border,#e2e8f0)">
      <div style="font-size:12px;color:var(--text-muted,#64748b)">${esc(r.source)} · <b style="color:var(--text-primary,#1e293b)">${esc(r.branch_name || '')}</b>${r.rating != null ? ` · ${r.rating}★` : ''} · ${r.age_days === 0 ? 'วันนี้' : (r.age_days || 0) + ' วัน'}${r.sla_breached ? ' <span style="color:var(--bad-ink);font-weight:600">เกิน SLA</span>' : ''}${r.contact ? ` · ${esc(r.contact)}` : ''}</div>
      <div style="font-size:13px;margin:3px 0">${r.text ? esc(r.text.slice(0, 160)) : '<i style="color:var(--text-muted,#64748b)">(ให้ดาวอย่างเดียว)</i>'}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap"><button onclick="msVoiceHandled(${r.id})" style="font-size:12px;padding:10px 14px;min-height:44px;border-radius:var(--r-md);border:1px solid #0f766e;background:#0f766e;color:#fff">✓ ตอบแล้ว</button>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener" style="font-size:12px;padding:10px 14px;min-height:44px;border-radius:var(--r-md);border:1px solid var(--border,#e2e8f0);color:var(--text-primary,#1e293b);text-decoration:none">เปิดต้นทาง ↗</a>` : ''}<button onclick="showFullDashboard('feedback')" style="font-size:12px;padding:10px 14px;min-height:44px;border-radius:var(--r-md);border:1px solid var(--border,#e2e8f0);background:transparent;color:var(--text-primary,#1e293b)">ตอบในแอป</button></div>
    </div>`).join('') : `<div style="font-size:12px;color:var(--text-muted,#64748b);padding:6px 0">ไม่มีรายการรอตอบ</div>`;
}
async function msVoiceHandled(id) {
  const note = prompt('ตอบลูกค้าอย่างไร (สรุปสั้น ๆ) — เว้นว่างได้');
  if (note === null) return;
  try {
    const r = await fetch(`${API}/api/feedback/${id}/handled`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note }) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    msLoadVoice();
  } catch (e) { alert('บันทึกไม่สำเร็จ: ' + e.message); }
}

// ── shared pieces (the summary view and the branch sheet print the same cards) ──
const _msYoyOk = j => !!(j && j.has_yoy && j.range && j.range[1] >= '2026-05-15');   // same guard as the report
const _msTgtCol = p => p == null ? '#94a3b8' : p >= 100 ? '#10b981' : p >= 90 ? '#f59e0b' : '#ef4444';
const _msSevCol = s => s === 'high' ? '#ef4444' : s === 'med' ? '#f59e0b' : '#94a3b8';
const _msCpCol = cp => cp == null ? '#94a3b8' : cp >= 70 ? '#ef4444' : cp >= 55 ? '#f59e0b' : '#10b981';
const _msMuted = 'color:var(--text-muted,#64748b)';
const _msNone = t => `<div class="ms-row" style="font-size:12px;${_msMuted}">${t}</div>`;
// the month in progress is compared on last month's weekday-fair baseline (owner rule; the report's
// headline.mom_basis === 'dow') - every % vs เดือนก่อน on the summary then reads on that basis
const _msDowNote = 'เดือนนี้ยังไม่ครบ จึงเทียบ % กับฐานปกติตามวันในสัปดาห์ของเดือนก่อน ไม่เทียบกับวันเดียวกันของเดือนก่อนโดยตรง';
const _msSM = v => (v >= 0 ? '+' : '−') + fmt(Math.abs(v || 0));
function msKpiTile(l, v, c, sub, span) {
  return `<div class="ms-kpi"${span ? ' style="grid-column:1/-1"' : ''}><p class="lbl">${l}</p><p class="val" style="${span ? '' : 'font-size:21px;'}color:${textInk(c)}">${v}</p><p class="sub">${sub || ''}</p></div>`;
}
// headline KPIs: revenue (+8-period columns), covers, spend/head, GP, discounts — MoM and YoY chips
function msKpisHtml(j) {
  const H = j.headline || {}, G = j.gp || {}, yoy = _msYoyOk(j);
  const chips = (m, y, vs) => msPchip(m) + (vs ? ` <span style="${_msMuted};font-size:12px">vs เดือนก่อน</span>` : '') + (yoy && y != null ? ` <span style="font-size:12px;${_msMuted}">· YoY</span> ${msPchip(y)}` : '');
  const tr = j.trend || [];
  const cols = tr.length >= 3 ? `<div style="margin-top:10px">${msColumns(tr.map(x => x.revenue ?? null), { partial: j.partial ? tr.length - 1 : -1, labels: tr.map(x => x.label), fmt, grain: 'month', height: 92, aria: 'รายได้รายเดือน' })}</div>` : '';
  return `<div class="ms-kpi" style="grid-column:1/-1"><p class="lbl">รายได้${j.partial ? ' (สะสม)' : ''}</p><p class="val" style="color:${textInk('#4a70b0')}">${fmt(H.revenue || 0)}</p><p class="sub">${chips(H.rev_wow_pct, H.rev_yoy_pct, true)}</p>${cols}</div>`
    + msKpiTile('ลูกค้า', fmtN(H.customers || 0), '#0891b2', chips(H.cust_wow_pct, H.cust_yoy_pct))
    + msKpiTile('รายได้/หัว', H.rev_per_head != null ? fmt(H.rev_per_head) : '—', '#7a66b8', chips(H.rph_wow_pct, H.rph_yoy_pct))
    + msKpiTile('กำไรขั้นต้น (GP)', G.gp != null ? fmt(G.gp) : '—', '#10b981', G.gp_pct != null ? `<b>${G.gp_pct.toFixed(0)}%</b> ${G.gp_pct_delta_pp != null ? msPchip(G.gp_pct_delta_pp, true, 'pp') : ''}` : `<span style="${_msMuted}">ไม่มีข้อมูลต้นทุน</span>`)
    + msKpiTile('ส่วนลด', fmt(H.discount || 0), '#ef4444', H.discount_pct != null ? `<b>${H.discount_pct.toFixed(1)}%</b> <span style="${_msMuted};font-size:12px">ของยอด${H.discount_policy_pct != null ? ` · เพดาน ${H.discount_policy_pct}%` : ''}</span>` : '')
    + (H.mom_basis === 'dow' ? `<p class="ms-dow-note" style="grid-column:1/-1;font-size:12px;${_msMuted};margin:0">${_msDowNote}</p>` : '');
}
// target card: % of target as the headline, the bar, then the figures behind it
function msTargetHtml(j) {
  const P = j.pace || {}, pvt = P.proj_vs_target_pct, mc = P.month_complete;
  if (!P.target) return _msNone('ยังไม่มีเป้าเดือนนี้ — สาขาใหม่ยังไม่มีเดือนเดียวกันปีก่อน');
  const c = _msTgtCol(pvt);
  return `<div style="display:flex;align-items:baseline;gap:10px;padding:10px 0 4px"><span style="font-size:30px;font-weight:800;line-height:1;color:${textInk(c)}">${pvt != null ? pvt.toFixed(0) + '%' : '—'}</span><span style="font-size:12px;${_msMuted}">${mc ? 'ของเป้าทั้งเดือน' : 'ของเป้า (ประมาณการสิ้นเดือน)'}</span></div>
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">${msBar(Math.min(100, Math.round(pvt || 0)), c)}<span style="font-size:12px;${_msMuted}">100%</span></div>
    <div class="ms-row"><span style="flex:1;font-size:13px">${mc ? 'ยอดทั้งเดือน' : `ยอดสะสม (${P.days_elapsed || 0}/${P.days_total || 0} วัน)`}</span><b style="font-variant-numeric:tabular-nums">${fmt(P.mtd_revenue || 0)}</b></div>
    ${mc ? '' : `<div class="ms-row"><span style="flex:1;font-size:13px">ประมาณการสิ้นเดือน${P.proj_method === 'dow' ? ' <span style="font-size:12px;' + _msMuted + '">(ถ่วงวันในสัปดาห์)</span>' : ''}</span><b style="font-variant-numeric:tabular-nums">${fmt(P.projected || 0)}</b></div>`}
    <div class="ms-row"><span style="flex:1;font-size:13px">เป้า ${P.month || ''}</span><b style="font-variant-numeric:tabular-nums">${fmt(P.target)}</b></div>
    ${P.mom_pct != null ? `<div class="ms-row"><span style="flex:1;font-size:13px">เทียบเดือนก่อน <span style="font-size:12px;${_msMuted}">${P.mom_basis === 'dow' ? '(ถ่วงวันในสัปดาห์)' : '(วันเท่ากัน)'}</span></span>${msPchip(P.mom_pct)}</div>` : ''}
    <p style="font-size:12px;${_msMuted};margin:6px 0 4px">เป้า = ตัวเลขที่ตั้งไว้ หรือยอดจริงของเดือนเดียวกันปีก่อน</p>`;
}
function msActionsHtml(j) {
  return (j.actions || []).map(a => `<div class="ms-row"><span style="width:8px;height:8px;border-radius:50%;background:${_msSevCol(a.sev)};flex-shrink:0;margin-top:2px;align-self:flex-start"></span><span style="flex:1;font-size:13px;color:var(--text-primary,#1e293b)">${esc(a.text)}</span></div>`).join('')
    || _msNone('ไม่พบประเด็นเร่งด่วน');
}
const _MS_DP = { Lunch: 'กลางวัน (11–14)', Dinner: 'เย็น (17–22)', Other: 'อื่นๆ' };
const _MS_DOW_EN = { Mon: 'จ.', Tue: 'อ.', Wed: 'พ.', Thu: 'พฤ.', Fri: 'ศ.', Sat: 'ส.', Sun: 'อา.' };
const _hh = h => String(h).padStart(2, '0') + ':00';
// peak hours (columns by hour), dayparts, weekday averages
function msPeakHtml(PK) {
  const hrs = ((PK || {}).hourly || []).filter(h => (h.revenue || 0) > 0);
  if (!hrs.length) return _msNone('ไม่มีข้อมูล');
  const top = hrs.slice().sort((a, b) => b.revenue - a.revenue).slice(0, 3).map(h => _hh(h.hour)).join(', ');
  const cols = msColumns(hrs.map(h => h.revenue), { labels: hrs.map(h => String(h.hour).padStart(2, '0')), fmt, height: 90, aria: 'รายได้รายชั่วโมง' });
  const dp = (PK.dayparts || []).map(d => `<div class="ms-row" style="padding:5px 0"><span style="width:104px;font-size:12px">${_MS_DP[d.name] || esc(d.name)}</span>${msBar(Math.round(d.pct || 0), '#0891b2')}<span style="width:38px;text-align:right;font-size:12px;font-variant-numeric:tabular-nums">${d.pct != null ? d.pct.toFixed(0) + '%' : '—'}</span></div>`).join('');
  const bd = PK.by_day || [], maxD = Math.max(1, ...bd.map(d => d.avg_revenue || 0));
  const days = bd.length ? `<p style="font-size:12px;${_msMuted};margin:8px 0 2px">รายได้เฉลี่ยต่อวัน ตามวันในสัปดาห์${PK.best_day ? ` · ดีสุด <b>${_MS_DOW_EN[PK.best_day] || PK.best_day}</b>` : ''}</p>`
    + bd.map(d => `<div class="ms-row" style="padding:4px 0"><span style="width:34px;font-size:12px;font-weight:${d.dow === PK.best_day ? 700 : 400}">${_MS_DOW_EN[d.dow] || d.dow}</span>${msBar(Math.round(100 * (d.avg_revenue || 0) / maxD), d.dow === PK.best_day ? '#4a70b0' : '#c9d5e8')}<span style="width:60px;text-align:right;font-size:12px;font-variant-numeric:tabular-nums">${fmt(d.avg_revenue || 0)}</span></div>`).join('') : '';
  return `<div style="padding-top:8px">${msGlanceLine(`พีค <b>${top}</b>${PK.peak_hour ? ` · ชั่วโมงทำเงินสูงสุด ${_hh(PK.peak_hour.hour)} (${fmt(PK.peak_hour.revenue)})` : ''}`)}</div>${cols}<p style="font-size:12px;${_msMuted};margin:2px 0 6px">แกนล่าง = ชั่วโมงของวัน</p>${dp}${days}`;
}
// tender mix (pos_payment_day) with the share change vs the comparison period
function msPaymentHtml(PY) {
  if (!PY || !PY.available || !(PY.channels || []).length) return _msNone('ไม่มีข้อมูลช่องทางชำระเงินช่วงนี้');
  const ch = PY.channels.slice(0, 7), pv = (PY.prev || {}).channels || {};
  return `<p style="font-size:12px;${_msMuted};margin:8px 0 2px">รวม ${fmt(PY.total)} · ${PY.channels.length} ช่องทาง · ตัวเลขท้าย = สัดส่วนเปลี่ยนจากเดือนก่อน</p>` + ch.map(c => {
    const p = pv[c.code], d = (p && p.pct != null) ? Math.round((c.pct - p.pct) * 10) / 10 : null;
    return `<div class="ms-row" style="padding:5px 0"><span style="width:88px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(c.code)}</span>${msBar(Math.round(c.pct), '#0d9488')}<span style="width:44px;text-align:right;font-size:12px;font-variant-numeric:tabular-nums">${c.pct.toFixed(1)}%</span><span style="width:48px;text-align:right;font-size:12px;${_msMuted}">${d != null && d !== 0 ? (d > 0 ? '+' : '') + d + 'pp' : ''}</span></div>`;
  }).join('');
}
// GP bridge: the three effects as signed bars
function msBridgeHtml(B, title) {
  if (!B) return '';
  const rows = [['จำนวนลูกค้า', B.volume, `${fmtN(B.covers_prev)} → ${fmtN(B.covers_cur)} หัว`],
                ['ยอดต่อหัว', B.spend_head, `฿${B.spend_head_prev.toFixed(0)} → ฿${B.spend_head_cur.toFixed(0)}`],
                ['ต้นทุนต่อหัว', B.cost_head, `฿${B.cost_head_prev.toFixed(0)} → ฿${B.cost_head_cur.toFixed(0)}`]];
  const mx = Math.max(1, ...rows.map(r => Math.abs(r[1])));
  return `<div style="padding:8px 0 2px;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap"><span style="font-size:12px;font-weight:700">${title}</span><span style="font-size:20px;font-weight:800;color:${textInk(B.delta >= 0 ? '#10b981' : '#ef4444')}">${_msSM(B.delta)}</span><span style="font-size:12px;${_msMuted}">GP ${fmt(B.gp_prev)} → ${fmt(B.gp_cur)}${B.gp_pct_prev != null ? ` · ${B.gp_pct_prev.toFixed(1)}% → ${B.gp_pct_cur.toFixed(1)}%` : ''}</span></div>`
    + rows.map(([l, v, s]) => `<div class="ms-row" style="padding:5px 0"><span style="width:100px;min-width:0"><span style="font-size:12px;display:block">${l}</span><span style="font-size:12px;${_msMuted}">${s}</span></span>${msBar(Math.round(100 * Math.abs(v) / mx), v >= 0 ? '#10b981' : '#ef4444')}<span style="width:64px;text-align:right;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;color:${textInk(v >= 0 ? '#10b981' : '#ef4444')}">${_msSM(v)}</span></div>`).join('');
}
// buffet price tiers: guests share, food cost/head, food cost %
function msTiersHtml(j) {
  const T = j.tier || {}, TR = T.rows || [];
  if (!TR.length) return _msNone('ไม่มีข้อมูลกลุ่มราคาช่วงนี้');
  return TR.map(t => `<div class="ms-row" style="padding:5px 0"><span style="width:88px;min-width:0"><span style="font-size:12px;font-weight:600;display:block">${esc(t.label)}</span><span style="font-size:12px;${_msMuted}">${fmtN(t.pax)} หัว${t.d_pax_pct != null ? ` (${t.d_pax_pct > 0 ? '+' : ''}${t.d_pax_pct}%)` : ''}</span></span>${msBar(Math.round(t.share || 0), '#b45309')}<span style="width:34px;text-align:right;font-size:12px">${(t.share || 0).toFixed(0)}%</span><span style="width:70px;text-align:right;font-size:12px;font-variant-numeric:tabular-nums;line-height:1.25"><b>฿${(t.cogs_head || 0).toFixed(0)}</b>/หัว<br><span style="color:${textInk(_msCpCol(t.food_pct))}">${t.food_pct != null ? t.food_pct.toFixed(0) + '%' : '—'}</span></span></div>`).join('')
    + `<p style="font-size:12px;${_msMuted};margin:6px 0 4px">แถบ = สัดส่วนลูกค้า · ฿/หัว = ต้นทุนอาหาร · % = food cost ของกลุ่มราคา${T.mix_effect != null ? ` · ผลจากการย้ายกลุ่มราคาต่อยอด/หัว ${T.mix_effect > 0 ? '+' : ''}฿${T.mix_effect}` : ''}</p>`;
}
// discount leak cards (bills, baht, change, top branches)
function msLeakCards(L) {
  const card = (title, o, hint) => {
    o = o || {}; if (o.unavailable) return '';
    const bad = (o.bills || 0) > 0, c = bad ? '#ef4444' : '#10b981';
    const top = (o.by_branch || []).slice(0, 2).map(x => esc(x.branch)).join(', ');
    return `<div class="ms-kpi" style="border:1px solid ${c}"><p class="lbl">${title}</p><p class="val" style="font-size:20px;color:${textInk(c)}">${fmtN(o.bills || 0)} <span style="font-size:12px;font-weight:400;${_msMuted}">บิล</span></p><p class="sub" style="font-size:12px">${fmt(o.baht || 0)}${o.bills_wow != null ? ' ' + msPchip(o.bills_wow, false) : ''}</p>${top ? `<p style="font-size:12px;${_msMuted}">${top}</p>` : ''}<p style="font-size:12px;${_msMuted}">${hint}</p></div>`;
  };
  return card('ลดหนัก ≥30%', L.heavy_discount, 'ลดตั้งแต่ 30% ของมูลค่าบิล') + card('ลดแล้วจ่ายเงินสด', L.cash_heavy, 'สัญญาณเสี่ยงเงินขาด')
    + card('บิลพิมพ์ซ้ำ', L.reprinted, 'บิลส่วนลดที่พิมพ์ >1 ครั้ง') + card('บิลยอดศูนย์', L.zero_gross, 'เหลือ ฿0 หลังลด');
}

// ── VIEW: สรุป ──
function msRenderSummary() {
  const j = _msRpt; if (!j) return;
  document.getElementById('msKpis').innerHTML = msKpisHtml(j);
  const tEl = document.getElementById('msTarget'); if (tEl) tEl.innerHTML = msTargetHtml(j);
  const aEl = document.getElementById('msActions'); if (aEl) aEl.innerHTML = msActionsHtml(j);
  const brs = j.branches || [], withMom = brs.filter(b => b.wow_pct != null);
  const best = withMom.slice().sort((a, b) => b.wow_pct - a.wow_pct)[0];
  const worst = withMom.slice().sort((a, b) => a.wow_pct - b.wow_pct)[0];
  const bwCard = (b, good) => !b ? '' : `<div class="ms-kpi" style="border:1px solid ${good ? '#10b981' : '#ef4444'}" data-br="${esc(b.branch)}" onclick="msOpenBranch(this.dataset.br)">
      <p class="lbl">${good ? 'โตสุด' : '⚠ ตกสุด'}</p>
      <p class="val" style="font-size:17px;color:var(--text-primary,#1e293b)">${esc(b.branch)} <span style="font-size:12px;${_msMuted}">›</span></p>
      <p class="sub">${msPchip(b.wow_pct)} · ${fmt(b.revenue)}</p></div>`;
  const bw = document.getElementById('msBestWorst');
  if (bw) { bw.innerHTML = brs.length > 1 ? bwCard(best, true) + bwCard(worst, false) : ''; bw.parentElement.style.display = brs.length > 1 ? '' : 'none'; }
  msLoadYesterday();
  msPushInit();
}

// ── เมื่อวาน: the Daily Brief's first card on the phone. The last finished day's
// revenue against the same weekday last week, its customers and bills, and the
// branches below their own normal — from the same /api/home/brief and
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
    wrap.style.display = '';
  } catch (e) {
    delete _msBriefCache[br]; _msScorecard = null;   // a failed call is not kept for next time
    wrap.style.display = 'none';
  }
}
function msYesterdayHtml(brief, sc, br) {
  const rev = brief.revenue_yesterday || 0, wow = brief.revenue_wow;
  const wowPct = wow ? Math.round((rev - wow) / wow * 1000) / 10 : null;
  const d = new Date(brief.yesterday + 'T00:00:00');
  const when = isNaN(d) ? brief.yesterday : `${_MS_TH_DOW[d.getDay()]} ${d.getDate()} ${_MS_TH_MON[d.getMonth()]}`;
  const rows = ((sc || {}).branches || []);
  // the Daily Brief's rule (js/07-home.js): status down, or 8 % or more under the branch's trailing average
  const down = rows.filter(b => b.status === 'down' || (b.vs_avg_pct != null && b.vs_avg_pct <= -8))
                   .sort((a, b) => (a.vs_avg_pct ?? 0) - (b.vs_avg_pct ?? 0));
  const bad = 'color:var(--bad-ink,#b91c1c)', good = 'color:var(--good-ink,#047857)';
  let line3 = '', go = '', target = '';
  if (br) {
    const me = rows.find(b => b.branch === br);
    if (me && me.vs_avg_pct != null) {
      line3 = me.vs_avg_pct <= -8
        ? `<span style="${bad}">ต่ำกว่าค่าปกติของสาขา ${Math.abs(me.vs_avg_pct).toFixed(0)}%</span>`
        : `<span style="${good}">อยู่ในเกณฑ์ปกติของสาขา (${me.vs_avg_pct >= 0 ? '+' : ''}${me.vs_avg_pct.toFixed(0)}% เทียบค่าเฉลี่ย 7 วัน)</span>`;
    }
    go = 'msOpenBranch(this.dataset.br)'; target = br;
  } else {
    line3 = down.length
      ? `<span style="${bad}">${down.length} สาขาต่ำกว่าปกติ</span> · ${down.slice(0, 2).map(b => `${esc(b.branch)} ${b.vs_avg_pct != null ? b.vs_avg_pct.toFixed(0) + '%' : ''}`).join(' · ')}`
      : `<span style="${good}">ทุกสาขาอยู่ในเกณฑ์ปกติ</span>`;
    if (down.length) { go = 'msOpenBranch(this.dataset.br)'; target = down[0].branch; }
    else go = "msSwitchView('branches')";
  }
  let skipNote = '';
  if (brief.skipped_day) {
    const sd = new Date(brief.skipped_day + 'T00:00:00');
    const sWhen = isNaN(sd) ? brief.skipped_day : `${sd.getDate()} ${_MS_TH_MON[sd.getMonth()]}`;
    skipNote = `<p style="font-size:12px;${_msMuted};margin-top:4px">ข้อมูลวันที่ ${esc(sWhen)} มีเพียง ${fmtN(brief.skipped_bills || 0)} บิล ยังมาไม่ครบ จึงแสดงวันก่อนหน้า</p>`;
  }
  return `<div data-br="${esc(target)}" onclick="${go}" role="button" aria-label="เมื่อวาน — แตะเพื่อดูรายสาขา" style="cursor:pointer;min-height:44px">
    <p style="font-size:12px;${_msMuted}">${brief.skipped_day ? 'วันล่าสุดที่ข้อมูลครบ' : 'เมื่อวาน'} · ${esc(when)}${br ? ' · ' + esc(br) : ''}</p>
    <p style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:2px"><b style="font-size:24px;line-height:1.1">${fmt(rev)}</b>${wowPct != null ? msPchip(wowPct) : ''}<span style="font-size:12px;${_msMuted}">เทียบวันเดียวกันสัปดาห์ก่อน</span></p>
    <p style="font-size:12px;${_msMuted};margin-top:2px">ลูกค้า ${fmtN(brief.customer_yesterday || 0)} · ${fmtN(brief.tx_yesterday || 0)} บิล · ${fmt(brief.avg_ticket_yesterday || 0)}/บิล</p>
    ${line3 ? `<p style="font-size:13px;margin-top:6px;display:flex;align-items:center;gap:6px"><span style="flex:1;min-width:0">${line3}</span><span style="${_msMuted}">›</span></p>` : ''}
    ${skipNote}
  </div>`;
}

// ── VIEW: สาขา ──
function msSetBranchSort(s) {
  _msBranchSort = s;
  ['rev', 'mom', 'rph', 'tgt'].forEach(x => document.getElementById('msSort' + x[0].toUpperCase() + x.slice(1))?.classList.toggle('ms-seg-on', x === s));
  msRenderBranches();
}
function msRenderBranches() {
  const j = _msRpt; if (!j) return;
  let branches = (j.branches || []).slice();
  const hasTgt = branches.some(b => b.vs_target_pct != null), yoy = _msYoyOk(j);
  const tgtBtn = document.getElementById('msSortTgt'); if (tgtBtn) tgtBtn.style.display = hasTgt ? '' : 'none';
  if (_msBranchSort === 'tgt' && !hasTgt) _msBranchSort = 'rev';
  const S = _msBranchSort;
  if (S === 'rev')      branches.sort((a, b) => (b.revenue || 0) - (a.revenue || 0));
  else if (S === 'mom') branches.sort((a, b) => (b.wow_pct ?? -1e9) - (a.wow_pct ?? -1e9));
  else if (S === 'rph') branches.sort((a, b) => (b.rev_per_head ?? -1e9) - (a.rev_per_head ?? -1e9));
  else                  branches.sort((a, b) => (b.vs_target_pct ?? -1e9) - (a.vs_target_pct ?? -1e9));
  const maxRev = Math.max(1, ...branches.map(b => b.revenue || 0));
  const revs = branches.map(b => b.revenue).filter(v => v != null);
  const avgW = revs.length >= 3 ? Math.round(100 * revs.reduce((a, v) => a + v, 0) / revs.length / maxRev) : null;
  const metric = S === 'mom' ? { v: b => b.wow_pct, o: { pct: true, fmt: v => `${v > 0 ? '+' : ''}${v}%` } }
               : S === 'rph' ? { v: b => b.rev_per_head || null, o: { fmt: v => fmt(v) + '/หัว', below: true } }
               : S === 'tgt' ? { v: b => b.vs_target_pct, o: { pct: true, fmt: v => `${v}% ของเป้า` } }
               : { v: b => b.revenue || null, o: { fmt, below: true } };   // 0 = not trading (e.g. a closed branch), not "the lowest"
  const sentence = branches.length > 1 ? glanceRangeSentence(branches.map(b => b.branch), branches.map(b => metric.v(b) ?? null), metric.o) : '';
  const withT = branches.filter(b => b.vs_target_pct != null), onT = withT.filter(b => b.vs_target_pct >= 100).length;
  const head = (sentence ? `<div style="padding-top:8px">${msGlanceLine(sentence)}</div>` : '')
    + `<p style="font-size:12px;${_msMuted};margin:0 0 6px">${hasTgt ? `<b style="color:${textInk(onT >= withT.length / 2 ? '#10b981' : '#f59e0b')}">${onT}/${withT.length}</b> สาขาถึงเป้า · ` : ''}แตะชื่อสาขาเพื่อดูรายละเอียด${(j.headline || {}).mom_basis === 'dow' ? ' · % เทียบเดือนก่อนอิงฐานปกติตามวันในสัปดาห์' : ''}</p>`;
  document.getElementById('msBranches').innerHTML = head + branches.map(b => {
    const right = S === 'rph'
      ? `<span style="font-size:13px;color:var(--text-secondary,#475569);font-variant-numeric:tabular-nums">${b.rev_per_head != null ? fmt(b.rev_per_head) : '—'}/หัว</span>`
      : S === 'tgt'
      ? `<span style="font-size:12px;${_msMuted}">${b.target != null ? 'เป้า ' + fmt(b.target) : ''}</span><span class="ms-chip" style="background:${_msTgtCol(b.vs_target_pct)}22;color:${textInk(_msTgtCol(b.vs_target_pct))}">${b.vs_target_pct != null ? b.vs_target_pct.toFixed(0) + '%' : '—'}</span>`
      : `<span style="font-size:13px;color:var(--text-secondary,#475569);font-variant-numeric:tabular-nums">${fmt(b.revenue)}</span>${msPchip(b.wow_pct)}`;
    const sub = [yoy && b.yoy_pct != null ? `YoY ${b.yoy_pct > 0 ? '+' : ''}${b.yoy_pct}%` : '',
                 b.vs_target_pct != null && S !== 'tgt' ? `<span style="color:${textInk(_msTgtCol(b.vs_target_pct))}">เป้า ${b.vs_target_pct.toFixed(0)}%</span>` : ''].filter(Boolean).join(' · ');
    return `<div class="ms-row" data-br="${esc(b.branch)}" onclick="msOpenBranch(this.dataset.br)" style="cursor:pointer">
      <span style="flex:1;min-width:0"><span style="font-size:13px;font-weight:500;color:var(--text-primary,#1e293b);display:block">${esc(b.branch)}${sub ? ` <span style="font-size:12px;font-weight:400;${_msMuted}">${sub}</span>` : ''}</span>
      <span style="display:flex;align-items:center;gap:6px;margin-top:3px">${msBar(Math.round(100 * (b.revenue || 0) / maxRev), '#4a70b0', avgW)}</span></span>
      <span style="display:flex;align-items:center;gap:6px;flex-shrink:0">${right}<span style="${_msMuted}">›</span></span>
    </div>`;
  }).join('') || '<div class="ms-row">ไม่มีข้อมูล</div>';
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
  : m.mom.basis === 'dow' ? 'เทียบฐานปกติตามวันในสัปดาห์ของเดือนก่อน'
  : m.mom.basis === 'same_days' ? 'เทียบวันเดียวกันของเดือนก่อน' : '';
function msMonthCell(label, value, mom, yoy) {
  return `<div style="min-width:0"><div style="font-size:12px;${_msMuted}">${label}</div>
    <div style="font-size:15px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--text-primary,#1e293b);white-space:nowrap">${value}</div>
    <div style="font-size:12px;white-space:nowrap">${mom}</div>${yoy != null ? `<div style="font-size:12px;white-space:nowrap"><span style="${_msMuted}">YoY</span> ${yoy}</div>` : ''}</div>`;
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
    const tag = m.mtd ? ` <span class="ms-chip" style="padding:1px 8px;font-size:12px;background:var(--bg-subtle);color:var(--text-secondary,#475569)">สะสม ถึงวันที่ ${_msYmd(m.through)[2]}</span>` : '';
    const note = _msMomBasisNote(m);
    const gpCell = showGp ? msMonthCell('GP %', m.gp_pct != null ? m.gp_pct.toFixed(1) + '%' : '—', m.gp_pct != null ? msMomTxt(M.gp_pp, true, 'pp') : `<span style="${_msMuted}">ไม่มีต้นทุน</span>`, null) : '';
    return `<div class="ms-mrow" role="button" tabindex="0" data-m="${esc(m.month)}" onclick="msPickMonth(this.dataset.m)" onkeydown="if(event.key==='Enter')msPickMonth(this.dataset.m)"
        style="padding:10px 0 12px;min-height:44px;cursor:pointer;border-top:1px solid var(--border,#e2e8f0);${on ? 'box-shadow:inset 3px 0 0 var(--group, var(--accent,#1f3a6b));padding-left:10px;margin-left:-10px;' : ''}">
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:6px"><b style="font-size:14px;color:var(--text-primary,#1e293b)">${esc(msPeriodLabel(m.range[0], 'month'))}</b>${tag}<span style="flex:1"></span><span style="font-size:12px;${_msMuted}">แตะเพื่อดูสรุป ›</span></div>
      ${note ? `<div style="font-size:12px;${_msMuted};margin:-2px 0 6px">${note}</div>` : ''}
      <div style="display:grid;grid-template-columns:repeat(${showGp ? 4 : 3},1fr);gap:6px">
        ${msMonthCell('รายได้', fmt(m.revenue), msMomTxt(M.revenue), Y ? msMomTxt(Y.revenue) : null)}
        ${msMonthCell('ลูกค้า', fmtN(m.customers), msMomTxt(M.customers), Y ? msMomTxt(Y.customers) : null)}
        ${msMonthCell('รายได้/หัว', m.rev_per_head != null ? fmt(m.rev_per_head) : '—', msMomTxt(M.rev_per_head), Y ? msMomTxt(Y.rev_per_head) : null)}
        ${gpCell}
      </div></div>`;
  }).join('');
  return `<div style="padding:10px 0 8px">${msGlanceLine(sentence)}${chart}</div>
    <p style="font-size:12px;${_msMuted};margin:0 0 6px">ตัวเลขใต้แท่ง และบรรทัดแรกใต้ตัวเลขในแต่ละเดือน = % เทียบเดือนก่อน · YoY = เทียบเดือนเดียวกันของปีก่อน · แท่งประ = เดือนปัจจุบัน (สะสม) เทียบกับฐานปกติตามวันในสัปดาห์ ไม่เทียบกับทั้งเดือน</p>
    ${cards}
    <p style="font-size:12px;${_msMuted};margin:6px 0 8px">รายได้ไม่รวม VAT และค่าบริการ · YoY แสดงเมื่อข้อมูลปีก่อนครบช่วงเทียบ${showGp ? ' · GP ใช้ต้นทุนสูตร (BoM) เฉพาะเมนูที่มีต้นทุนแล้ว ต้นทุนจึงเป็นค่าขั้นต่ำ' : ''}</p>`;
}
async function msLoadMonths(wrap) {
  wrap.innerHTML = '<div class="ms-row">กำลังโหลด…</div>';
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
  wrap.innerHTML = '<div class="ms-row">กำลังโหลด…</div>';
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
    const tag = inProg ? ` <span class="ms-chip" style="padding:1px 6px;font-size:12px;background:var(--bg-subtle);color:var(--text-secondary,#475569)">${gran === 'day' ? 'วันนี้' : gran === 'week' ? 'สัปดาห์นี้' : 'MTD'}</span>` : '';
    return `<div class="ms-row" style="${wkend ? 'background:var(--bg-subtle);margin:0 -16px;padding-left:16px;padding-right:16px' : ''}">
      <span style="width:${gran === 'month' ? 58 : 82}px;font-size:12px;font-weight:${latest ? '700' : '400'};color:${latest ? 'var(--text-primary,#1e293b)' : 'var(--text-muted,#64748b)'}">${lblOf(x)}</span>
      ${msBar(w, inProg ? 'var(--border,#cbd5e1)' : latest ? 'var(--group, var(--accent,#1f3a6b))' : 'color-mix(in srgb, var(--group, var(--accent,#1f3a6b)) 38%, transparent)', avgW)}
      <span style="width:${inProg ? 104 : 64}px;text-align:right;font-size:12px;font-weight:${latest ? '700' : '400'};font-variant-numeric:tabular-nums;color:var(--text-primary,#1e293b)">${fmt(x.revenue || 0)}${tag}</span>
    </div>`;
  }).join('');
  wrap.innerHTML = rows
    ? `<div style="padding:10px 0 8px">${msTrendBlock(asc, gran, 110)}</div>
       <p style="font-size:12px;${_msMuted};margin:0 0 2px">ขีดในแถบ = ค่าเฉลี่ย${gran === 'day' ? ' · แถบเทา = เสาร์-อาทิตย์' : gran === 'week' ? ' · สัปดาห์ จ.–อา.' : ''}</p>${rows}`
    : '<div class="ms-row">ไม่มีข้อมูล</div>';
}
function msRenderPeakPay() {
  const j = _msRpt; if (!j) return;
  const pk = document.getElementById('msPeak'), py = document.getElementById('msPayment');
  if (pk) pk.innerHTML = msPeakHtml(j.peak);
  if (py) py.innerHTML = msPaymentHtml(j.payment);
}

// ── VIEW: เมนู ──
async function msLoadMenu() {
  const wrap = document.getElementById('msMenu');
  const rWrap = document.getElementById('msMenuRisers');
  wrap.innerHTML = '<div class="ms-row">กำลังโหลด…</div>';
  if (rWrap) rWrap.innerHTML = '<div class="ms-row">กำลังโหลด…</div>';
  // limit=100: one payload feeds BOTH lists — top-20 by qty (below) and the
  // MoM risers. The top-100-by-qty pool doubles as a volume floor so a tiny-
  // base SKU can't fake a huge % (debug-mantra rule 5); qty_mom_pct itself is
  // MTD-fair (same day-range both months) and the food filter is the canonical
  // _FOOD_CATEGORY_FILTER incl. the durian whitelist (rule 23).
  const q = new URLSearchParams({ sort: 'qty', limit: '100' });
  const br = msBranch(); if (br) q.set('branch', br);
  const mon = (_msRpt && _msRpt.selected_month) || ''; if (mon) q.set('month', mon);
  let d; try { d = await api(`${API}/api/sales/top_ordered?${q}`); } catch (e) { wrap.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; if (rWrap) rWrap.innerHTML = ''; return; }
  const pool = (d.items || []);
  if (rWrap) rWrap.innerHTML = msRisersHtml(pool, d, 1000, 8);
  wrap.innerHTML = msTopMenuHtml(pool.slice(0, 20));
}
// มาแรง: biggest positive qty-MoM among meaningful-volume menus
function msRisersHtml(pool, d, minQty, n) {
  const risers = pool.filter(x => (x.qty || 0) >= minQty && x.qty_mom_pct != null && x.qty_mom_pct > 0 && x.qty_mom_pct <= 400)
    .sort((a, b) => b.qty_mom_pct - a.qty_mom_pct).slice(0, n);
  const maxM = Math.max(1, ...risers.map(x => x.qty_mom_pct || 0));
  const capNote = (d || {}).mom_basis === 'mtd'
    ? `เทียบช่วงวันที่ 1–${d.mtd_cutoff_day || ''} ของเดือนก่อน (แฟร์กับเดือนที่ยังไม่จบ)` : 'เทียบเดือนก่อนหน้า';
  return (risers.map((x, i) => {
    const nm = (x.description || x.sku_code || '').replace(/^\((?:A|B)\)\s*/, '');
    return `<div class="ms-row">
      <span style="width:18px;font-size:12px;${_msMuted}">${i + 1}</span>
      <span style="flex:1;min-width:0"><span style="font-size:13px;color:var(--text-primary,#1e293b);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(nm)}</span>
      <span style="display:flex;align-items:center;gap:6px;margin-top:3px">${msBar(Math.round(100 * (x.qty_mom_pct || 0) / maxM), '#10b981')}</span></span>
      <span style="width:84px;text-align:right;flex-shrink:0">${msPchip(Math.round(x.qty_mom_pct * 10) / 10)}<br><span style="font-size:12px;${_msMuted};font-variant-numeric:tabular-nums">${fmtN(x.qty || 0)} ชิ้น</span></span>
    </div>`;
  }).join('') || '<div class="ms-row">ยังไม่มีเมนูยอดนิยมที่เพิ่มขึ้นชัดเจน</div>')
    + `<div class="ms-row" style="border:0;padding-top:6px"><span style="font-size:12px;${_msMuted}">${capNote} · เฉพาะเมนูยอด ≥ ${fmtN(minQty)} ชิ้น · ไม่นับเมนูที่เพิ่งเริ่มขาย (>5 เท่า)</span></div>`;
}
function msTopMenuHtml(items) {
  const maxQ = Math.max(1, ...items.map(x => x.qty || 0));
  return items.map((x, i) => {
    const nm = (x.description || x.sku_code || '').replace(/^\((?:A|B)\)\s*/, '');
    return `<div class="ms-row">
      <span style="width:18px;font-size:12px;${_msMuted}">${i + 1}</span>
      <span style="flex:1;min-width:0"><span style="font-size:13px;color:var(--text-primary,#1e293b);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(nm)}</span>
      <span style="display:flex;align-items:center;gap:6px;margin-top:3px">${msBar(Math.round(100 * (x.qty || 0) / maxQ), '#0891b2')}</span></span>
      <span style="width:74px;text-align:right;flex-shrink:0"><span style="font-size:13px;font-weight:600;font-variant-numeric:tabular-nums">${fmtN(x.qty || 0)}</span><br>${msPchip(x.qty_mom_pct)}</span>
    </div>`;
  }).join('') || '<div class="ms-row">ไม่มีข้อมูล</div>';
}

// ── VIEW: กำไร/ต้นทุน (GP, food cost, the bridge, price tiers, cost/head by branch) ──
function msProfitKpisHtml(j) {
  const H = j.headline || {}, P = j.pace || {}, G = j.gp || {}, M = j.menu || {};
  const fcPrev = P.food_cost_pct_prev;
  return [
    { l: 'GP %', v: G.gp_pct != null ? G.gp_pct.toFixed(1) + '%' : '—', c: '#10b981', s: G.gp_pct_delta_pp != null ? `${G.gp_pct_delta_pp > 0 ? '+' : ''}${G.gp_pct_delta_pp}pp vs เดือนก่อน` : (G.cost_basis === 'unavailable' ? 'ไม่มีข้อมูลต้นทุน' : '') },
    { l: 'Food cost % (BoM)', v: P.food_cost_pct != null ? P.food_cost_pct.toFixed(1) + '%' : '—', c: _msCpCol(P.food_cost_pct), s: fcPrev != null ? `เดือนก่อน ${fcPrev.toFixed(1)}%` : '' },
    { l: 'ต้นทุนอาหาร/หัว', v: M.total_cph != null ? fmt(M.total_cph) : '—', c: '#ef4444', s: M.total_cph_delta != null ? `${M.total_cph_delta > 0 ? '+' : M.total_cph_delta < 0 ? '−' : ''}฿${Math.abs(M.total_cph_delta).toFixed(1)} vs เดือนก่อน` : '' },
    { l: 'GP/หัว', v: G.gp_per_head != null ? fmt(G.gp_per_head) : '—', c: '#0891b2', s: H.rev_per_head != null ? `รายได้/หัว ${fmt(H.rev_per_head)}` : '' },
  ].map(k => `<div class="ms-kpi"><p class="lbl">${k.l}</p><p class="val" style="font-size:20px;color:${textInk(k.c)}">${k.v}</p><p class="sub" style="font-size:12px;${_msMuted}">${k.s}</p></div>`).join('');
}
function msCoverageNote(j) {
  const cc = (j.pace || {}).cost_coverage;
  return (cc && cc.coverage_pct != null && cc.coverage_pct < 99.5)
    ? `<p style="font-size:12px;color:var(--amber-ink,#b45309);margin:4px 0 0">ต้นทุนครอบคลุม ${cc.coverage_pct}% ของจาน${cc.est_food_cost_pct != null ? ` · ประมาณการ food cost จริง ~${cc.est_food_cost_pct}%` : ''} — ตัวเลขต้นทุนจึงเป็นขั้นต่ำ</p>` : '';
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
  br.innerHTML = '<div class="ms-row">กำลังโหลด…</div>';
  const q = new URLSearchParams({ gran: 'month' });
  const b = msBranch(); if (b) q.set('branch', b);
  if (j.range && j.range[1]) q.set('date', j.range[1]);   // the month on screen, not the latest data day (one branch's sliver)
  let d; try { d = await api(`${API}/api/sales/cost-per-head?${q}`); } catch (e) { br.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; return; }
  if (_msRpt !== j) return;
  const branches = (d.branches || []).filter(r => r.cost_pct != null).sort((a, b2) => b2.cost_pct - a.cost_pct);   // worst first
  const over = branches.filter(r => r.cost_pct >= 55).length;
  const sentence = branches.length > 1 ? glanceRangeSentence(branches.map(r => r.branch), branches.map(r => r.cost_pct), { pct: true, fmt: v => v + '%' }) : '';
  br.innerHTML = (sentence ? `<div style="padding-top:8px">${msGlanceLine(`Food cost ${sentence}${over ? ` · <b style="color:var(--bad-ink)">${over} สาขา ≥ 55%</b>` : ''}`)}</div>` : '') + branches.map(r => `<div class="ms-row" data-br="${esc(r.branch)}" onclick="msOpenBranch(this.dataset.br)" style="cursor:pointer">
      <span style="flex:1;font-size:13px;color:var(--text-primary,#1e293b)">${esc(r.branch)}</span>
      <span style="font-size:12px;${_msMuted};font-variant-numeric:tabular-nums">${r.cost_per_head != null ? fmt(r.cost_per_head) : '—'}/หัว</span>
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
    `<div class="ms-kpi" style="grid-column:1/-1"><p class="lbl">ส่วนลดรวม${j.partial ? ' (สะสม)' : ''}</p><p class="val" style="color:${textInk(c)}">${fmt(H.discount || 0)}</p><p class="sub"><b style="color:${textInk(c)}">${H.discount_pct != null ? H.discount_pct.toFixed(1) + '%' : '—'}</b> <span style="${_msMuted};font-size:12px">ของยอดก่อนลด${pol != null ? ` · เพดาน ${pol}%` : ''}${over ? ` · เกินเพดาน ${fmt(over)}` : ''}</span></p></div>`;
  document.getElementById('msDiscCards').innerHTML = msLeakCards(L) || _msNone('ไม่มีข้อมูลส่วนลดช่วงนี้');
  const mb = L.by_master_branch || [], maxP = Math.max(1, ...mb.map(x => x.pct || 0));
  document.getElementById('msDiscBranches').innerHTML = mb.length ? mb.map(x => {
    const bad = pol != null && x.pct > pol;
    return `<div class="ms-row" data-br="${esc(x.branch)}" onclick="msOpenBranch(this.dataset.br)" style="cursor:pointer;padding:6px 0"><span style="width:88px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(x.branch)}</span>${msBar(Math.round(100 * (x.pct || 0) / maxP), bad ? '#ef4444' : '#f59e0b')}<span style="width:40px;text-align:right;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;color:${textInk(bad ? '#ef4444' : '#475569')}">${x.pct != null ? x.pct.toFixed(1) + '%' : '—'}</span><span style="width:58px;text-align:right;font-size:12px;${_msMuted};font-variant-numeric:tabular-nums">${fmt(x.disc)}</span></div>`;
  }).join('') + `<p style="font-size:12px;${_msMuted};margin:6px 0 4px">เฉพาะสาขาบริษัท · % = ส่วนลด ÷ ยอดขายของสาขา · แตะเพื่อดูสาขา</p>` : _msNone('ไม่มีข้อมูลรายสาขา');
}

// ── BRANCH SHEET: tap a branch anywhere → everything about it for the month ──
async function msOpenBranch(name) {
  if (!name) return;
  const sh = document.getElementById('msSheet'), body = document.getElementById('msSheetBody');
  if (!sh || !body) return;
  document.getElementById('msSheetTitle').textContent = name;
  document.getElementById('msSheetSub').textContent = (_msRpt && _msRpt.label) || '';
  sh.classList.add('open'); document.body.classList.add('ms-sheet-open'); sh.scrollTop = 0;
  body.innerHTML = '<div class="ms-section"><div class="ms-card"><div class="ms-row">กำลังโหลด…</div></div></div>';
  const mon = msMonth();
  const dq = new URLSearchParams({ gran: 'day', trend: '14', branch: name });
  const mq = new URLSearchParams({ sort: 'qty', limit: '40', branch: name });
  if (msPickedOlderMonth()) dq.set('date', _msRpt.range[1]);
  if (_msRpt && _msRpt.selected_month) mq.set('month', _msRpt.selected_month);
  const canMenu = _msMod('menu.analysis') || _msMod('cost.analysis');
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
  const sh = document.getElementById('msSheet'); if (sh) sh.classList.remove('open');
  document.body.classList.remove('ms-sheet-open');
}
function msBranchSheetHtml(j, days, menu) {
  const sec = (title, inner, grid) => `<div class="ms-section"><div class="ms-section-title">${title}</div>${grid ? `<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">${inner}</div>` : `<div class="ms-card">${inner}</div>`}</div>`;
  const H = j.headline || {}, G = j.gp || {}, M = j.menu || {}, L = j.leak || {};
  const cost = _msMod('cost.analysis') || _msMod('profit.pnl');
  const dtr = (days || {}).trend || [];
  const trend14 = dtr.length >= 3 ? `<div style="padding:8px 0 4px">${msTrendBlock(dtr, 'day', 100)}</div>` : _msNone('ไม่มีข้อมูลรายวัน');
  const pool = (menu || {}).items || [];
  const menuHtml = pool.length ? `<p style="font-size:12px;${_msMuted};margin:8px 0 0">ขายดี (ตามจำนวน)</p>${msTopMenuHtml(pool.slice(0, 8))}<p style="font-size:12px;${_msMuted};margin:10px 0 0">มาแรง (คนกินเพิ่มขึ้น)</p>${msRisersHtml(pool, menu, 200, 5)}` : '';
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
    + (menuHtml ? sec('เมนู', menuHtml) : '')
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
    if (wGp) chips.push({ icon:'', label:'กำไร(margin)ดีสุด', val:esc(wGp.label),
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
  Chart.defaults.font.size = 11.5;
  Chart.defaults.color = '#475569';
  Chart.defaults.devicePixelRatio = Math.min(window.devicePixelRatio || 1, 2); // crisp lines

  // Rounded, cleaner bars
  Chart.defaults.elements.bar.borderRadius = 7;      // rounded tip (owner 2026-10-01: softer, livelier bars)...
  Chart.defaults.elements.bar.borderSkipped = 'start'; // ...sitting flush on the baseline
  Chart.defaults.elements.bar.maxBarThickness = 46;

  // Smoother lines, smaller resting points that grow on hover (less clutter)
  Chart.defaults.elements.line.tension = 0.25;
  Chart.defaults.elements.line.borderWidth = 2;
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
