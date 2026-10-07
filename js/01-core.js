// ─── 01-core.js — slice 1/9 of the former app.js (P2 structure refactor) ───
// Sequential load order matters: index.html loads 01→09; classic scripts
// share one global scope, so this equals the original single file.

// Stroke icons for icon-only buttons (24 grid, 1.75 stroke, the nav icons' style). Formal theme:
// no emoji as icons. Style: .ui-icon in docs/ui-kit/theme-formal.css.
const _svgIcon = d => `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const ICON = {
  search: _svgIcon('<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'),
  moon: _svgIcon('<path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/>'),
  sun: _svgIcon('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  key: _svgIcon('<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2M16 7l3 3M18 5l2 2"/>'),
  trash: _svgIcon('<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6M10 11v6M14 11v6M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/>'),
  save: _svgIcon('<path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>'),
  phone: _svgIcon('<rect x="6" y="2" width="12" height="20" rx="3"/><line x1="11" y1="18" x2="13" y2="18"/>'),
  print: _svgIcon('<polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>'),
  download: _svgIcon('<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'),
};

// Oshinei Dashboard — application JS (split out of index.html,
// lean-overhaul Phase 4, 2026-07-20). Served at /app.js with
// Cache-Control: no-cache so deploys can never leave stale JS cached.
// This file IS the app: index.html holds markup/CSS only.
// ── Config & globals ───────────────────────────────────────────
// When opened as a local file (file://) point API calls at the running backend
const API = location.protocol === 'file:' ? 'http://localhost:8000' : '';

// ── Write-auth: attach the owner token to mutating /api/ calls ────────────────
// Hosted deploys gate every data-writing endpoint behind X-Upload-Token (the
// same token as upload/seed). Local dev leaves OSHINEI_SEED_TOKEN unset, so the
// server allows writes without a token and this stays invisible. /api/upload
// manages its own token (see handleUpload) and is excluded here. Read-only POSTs
// like /api/bom-cost/simulate aren't gated server-side, so they never prompt.
// The token is asked for in an in-page dialog, not window.prompt(): embedded
// browsers (the Claude app's browser pane, some in-app webviews) do not support
// prompt(), and a save then failed with "prompt() is not supported".
window.askWriteToken = function (message) {
  // several saves can hit 403 together: they all wait for the one dialog
  if (window._askTokenPending) return window._askTokenPending;
  return (window._askTokenPending = new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(15,23,42,.45);display:flex;align-items:center;justify-content:center;padding:16px';
    wrap.innerHTML = `<form style="width:100%;max-width:380px;background:var(--bg-card, var(--bg-input, #fff));color:var(--text-primary);border:1px solid var(--border);border-radius:var(--r-md, 12px);padding:16px;box-shadow:0 10px 30px rgba(0,0,0,.25)">
        <div style="font-weight:600;font-size:15px;margin-bottom:6px">ยืนยันสิทธิ์บันทึกข้อมูล</div>
        <div style="font-size:13px;color:var(--text-secondary);margin-bottom:10px;white-space:pre-line"></div>
        <input type="password" autocomplete="off" spellcheck="false" placeholder="Owner Write-Token"
               style="width:100%;box-sizing:border-box;border:1px solid var(--border-control, var(--border));border-radius:var(--r-sm, 8px);padding:8px 10px;font-size:14px;background:var(--bg-input, #fff);color:var(--text-primary)">
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button type="button" data-act="cancel" style="border:1px solid var(--border);border-radius:var(--r-sm, 8px);padding:6px 14px;font-size:13px;cursor:pointer;background:transparent;color:var(--text-primary)">ยกเลิก</button>
          <button type="submit" style="border:0;border-radius:var(--r-sm, 8px);padding:6px 14px;font-size:13px;cursor:pointer;background:var(--accent);color:var(--on-accent, #fff)">ยืนยัน</button>
        </div></form>`;
    wrap.querySelector('form > div:nth-child(2)').textContent = message || '';
    const input = wrap.querySelector('input');
    const done = v => { wrap.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = e => { if (e.key === 'Escape') done(''); };
    wrap.querySelector('form').addEventListener('submit', e => { e.preventDefault(); done(input.value.trim()); });
    wrap.querySelector('[data-act="cancel"]').addEventListener('click', () => done(''));
    document.addEventListener('keydown', onKey);
    document.body.appendChild(wrap);
    setTimeout(() => input.focus(), 0);
  }).finally(() => { window._askTokenPending = null; }));
};

(function () {
  const TOK_KEY = 'oshinei_upload_token';
  const _origFetch = window.fetch.bind(window);
  const isWrite = m => /^(POST|PUT|DELETE|PATCH)$/i.test(m || 'GET');
  // When an authenticated session expires (12h), every /api/ call starts 401-ing.
  // Catch it once and re-open the login overlay instead of letting the SPA break
  // silently into half-loaded charts + a generic error toast.
  const observe = (url, res) => {
    if (res && res.status === 401 && url.includes('/api/') && !url.includes('/api/auth/')
        && window.AUTH && window.AUTH.enabled) {
      try { onSessionExpired(); } catch (e) {}
    }
    return res;
  };
  window.fetch = function (input, init) {
    init = init || {};
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const method = init.method || (typeof input !== 'string' && input && input.method) || 'GET';
    if (!isWrite(method) || !url.includes('/api/') || url.includes('/api/upload')) {
      return _origFetch(input, init).then(r => observe(url, r));
    }
    const send = (tok) => {
      const h = new Headers(init.headers || (typeof input !== 'string' && input ? input.headers : null) || {});
      if (tok) h.set('X-Upload-Token', tok);
      return _origFetch(input, Object.assign({}, init, { headers: h }));
    };
    return send(localStorage.getItem(TOK_KEY) || '').then(async res => {
      if (res.status !== 403) return observe(url, res);
      const tok = await window.askWriteToken('การบันทึกข้อมูลต้องใช้ Owner Write-Token\nกรอกครั้งเดียว เบราว์เซอร์นี้จะจำไว้');
      if (!tok) return res;                       // cancelled → surface the 403
      localStorage.setItem(TOK_KEY, tok);
      return send(tok).then(res2 => {
        if (res2.status === 403) localStorage.removeItem(TOK_KEY);  // wrong token
        return res2;
      });
    });
  };
})();

// Session-expiry handler — re-open the login overlay (guarded so a burst of
// concurrent 401s only prompts once).
let _sessionExpiredShown = false;
function onSessionExpired() {
  if (_sessionExpiredShown) return;
  _sessionExpiredShown = true;
  if (window.AUTH) window.AUTH.authenticated = false;
  const err = document.getElementById('loginError');
  if (err) { err.textContent = 'เซสชันหมดอายุ — กรุณาเข้าสู่ระบบอีกครั้ง'; err.style.display = 'block'; }
  if (typeof showLogin === 'function') showLogin();
}

// ── Role-based authentication (client side; server enforces too) ─────────────
// window.AUTH = { enabled, authenticated, username, role, modules[] }. When the
// server has OSHINEI_AUTH off, /api/auth/me reports enabled:false + all modules,
// so nothing below hides anything (current behaviour preserved).
window.AUTH = { enabled: false, authenticated: true, role: 'management', modules: [] };

// Sidebar tab → module it requires. Tabs absent from the map (e.g. 'manual') are
// always allowed to any logged-in user. Mirrors MODULE_RULES in backend/auth.py.
const TAB_MODULE = {
  home:'sales.overview', overall:'sales.overview', action:'sales.overview',
  dailysales:'sales.overview', trend:'sales.overview', compare:'sales.overview', more:'sales.overview',
  buffetperf:'customer.buffet', childbuffet:'customer.buffet', guest:'customer.buffet', timeflow:'customer.buffet',
  menu:'menu.analysis', engineering:'menu.analysis', menucompare:'menu.analysis', menuefficiency:'cost.analysis',
  menuactionboard:'cost.analysis', menuopportunities:'menu.analysis', feedback:'customer.feedback', checklist:'customer.feedback',
  cost:'cost.analysis', bomcost:'cost.analysis', costhead:'cost.analysis', costheadtop60:'cost.analysis',
  menuimpact:'cost.analysis',
  heavyeater:'cost.analysis', coverage:'cost.analysis', branchbom:'cost.analysis',
  simhub:'cost.simulator',
  gpbranch:'profit.pnl', pnlbranch:'profit.pnl', pnlbill:'profit.pnl', profit:'profit.pnl', benchmark:'profit.pnl',
  pnl:'profit.pnl',   // P&L เชิงลึก - back in the กำไร bar 2026-10-01; /api/pnl is profit.pnl on the server too
  opsreport:'ops.report', ops:'ops.report',
  // จัดซื้อ/วัตถุดิบ: any ONE of the listed modules opens the tab (supply.ordering = Sales Support)
  ordering:['ops.report','supply.ordering'], ordersetup:['ops.report','supply.ordering'],
  ingpurchase:['cost.analysis','supply.ordering'],   // ยอดซื้อวัตถุดิบ vs ยอดขาย — เป็นตัวเลขต้นทุน
  bomcenter:['cost.analysis','supply.ordering','bom.center','bom.edit','bom.release','bom.audit'],
  branchstock:['branch.stock','supply.ordering','bom.center','ops.report'],   // Phase 2: สาขาของฉัน — นับสต๊อก
  orderhub:['supply.ordering','ops.report','bom.center'],   // Phase 2 S9: ศูนย์สั่งของ — คิว Sales Support · compliance · สรุปเดือน   // BoM Center (S1): ทะเบียน ราคา หน่วย
  staff:'ops.staff', discounts:'ops.discounts',
  cashctl:'ops.cash',
  custtype:'ops.posextra', modifiers:'ops.posextra', pricezone:'ops.posextra',
  cancelbill:'ops.posextra', closetime:'ops.posextra',
  report:'report.export', mgtreport:'report.export', settings:'system.settings',
};
// ── First-login start card: the three tabs each role should open first ──────
// Shown once, on the first login that has never opened a tab (/api/usage/me →
// first_time), on top of the home page; "เข้าใจแล้ว" dismisses it for good.
const ROLE_START = {
  management: [['home', 'ภาพรวมวันนี้ สัญญาณที่ต้องดู และคิวงานของวัน'], ['mgtreport', 'รายงานผู้บริหาร รายวัน / สัปดาห์ / เดือน พร้อม export'], ['gpbranch', 'กำไรขั้นต้นรายสาขา เทียบกันได้ทันที']],
  accounting: [['discounts', 'ส่วนลด การรั่วไหล และ ฿ ที่เกินเพดานนโยบาย'], ['opsreport', 'การชำระเงิน บิลยกเลิก โต๊ะ โปรโมชัน'], ['mgtreport', 'ตัวเลขสรุปสำหรับปิดงวด พร้อม Excel']],
  operation:  [['opsreport', 'ปฏิบัติการรายวัน: ชำระเงิน void โต๊ะ'], ['ordering', 'แผนสั่งวัตถุดิบรายสาขาเป็นแพ็ก'], ['discounts', 'ส่วนลดของสาขาเทียบเพดาน']],
  marketing:  [['feedback', 'เสียงลูกค้าจากทุกช่องทาง + งานที่ต้องตอบ'], ['menu', 'เมนูขายดี / ขายไม่ออก'], ['buffetperf', 'ผลงานบุฟเฟ่ต์และยอดต่อหัว']],
  analyst:    [['overall', 'KPI ภาพรวมและ heatmap รายสาขา'], ['dailysales', 'ยอดขายรายวัน เทียบเดือน / ปีก่อน'], ['cost', 'ต้นทุนและ food cost']],
  innovation: [['menuactionboard', 'เมนูที่ควรปรับ 4 กลุ่ม'], ['menuimpact', 'วัตถุดิบ: มี vs ไม่มี กระทบอะไร'], ['menu', 'วิเคราะห์เมนูเชิงลึก']],
  salessupport: [['orderhub', 'คิวใบสั่งซื้อจากสาขา: ดำเนินการ สรุปรวมตามผู้จำหน่าย และส่งออก Excel'], ['ingpurchase', 'กรอก/นำเข้ายอดซื้อวัตถุดิบรายสัปดาห์ เทียบยอดขาย'], ['ordering', 'แผนสั่งวัตถุดิบรายสาขาเป็นแพ็ก']],
  branch: [['branchstock', 'ตรวจนับสินค้าคงเหลือของสาขาทีละโซน']],
};
function _startKey() { return 'startCard:' + ((window.AUTH && window.AUTH.username) || 'anon'); }
function showStartCard(role) {
  const items = (ROLE_START[role] || ROLE_START.management).filter(([t]) => tabAllowed(t)).slice(0, 3);
  const host = document.getElementById('tab-home'); if (!host || !items.length) return;
  let card = document.getElementById('homeStartCard');
  if (!card) { card = document.createElement('div'); card.id = 'homeStartCard'; host.insertBefore(card, host.firstChild); }
  card.className = 'card p-5 mb-5'; card.style.borderLeft = '4px solid var(--indigo)';
  card.innerHTML = `<div class="flex items-start justify-between gap-3"><div>
      <div class="text-base font-bold" style="color:var(--text-primary)">เริ่มต้นใช้งาน — ${esc(ROLE_LABEL_TH[role] || role)}</div>
      <div class="text-xs mt-1" style="color:var(--text-muted)">สามหน้าที่บทบาทนี้ใช้บ่อยที่สุด กดเปิดได้เลย — การ์ดนี้แสดงจนกว่าจะกด "เข้าใจแล้ว"</div></div>
      <button onclick="dismissStartCard()" class="text-xs px-3 py-1 rounded-lg whitespace-nowrap" style="border:1px solid var(--border);color:var(--text-secondary)">เข้าใจแล้ว ✕</button></div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mt-4">${items.map(([t, why], i) => `<button onclick="switchTab('${t}')" class="text-left p-3 rounded-xl transition-colors hover:ring-2 hover:ring-indigo-200" style="background:var(--bg-input);border:1px solid var(--border)">
      <div class="text-[11px] font-semibold" style="color:var(--indigo)">ขั้นที่ ${i + 1}</div><div class="font-semibold text-sm mt-0.5" style="color:var(--text-primary)">${esc(PAGE_TITLES[t] || t)}</div><div class="text-xs mt-1" style="color:var(--text-muted)">${esc(why)}</div></button>`).join('')}</div>
    <div class="text-xs mt-3" style="color:var(--text-muted)">คู่มือฉบับเต็ม: <a href="#" onclick="switchTab('manual');return false" class="underline">คู่มือการใช้งาน</a></div>`;
  card.hidden = false;
}
function dismissStartCard() {
  try { localStorage.setItem(_startKey(), '1'); } catch(e) {}
  const c = document.getElementById('homeStartCard'); if (c) c.remove();
}
async function maybeShowStartCard() {
  if (!window.AUTH || !window.AUTH.enabled || !window.AUTH.authenticated) return;
  try { if (localStorage.getItem(_startKey())) return; } catch(e) {}
  let me = null;
  try { me = await api(`${API}/api/usage/me`); } catch(e) { return; }
  if (me && me.first_time && tabAllowed('home')) { switchTab('home'); showStartCard(window.AUTH.role || me.role); }
}

const ROLE_LABEL_TH = {
  management:'ผู้บริหาร', marketing:'การตลาด', operation:'ปฏิบัติการ',
  analyst:'นักวิเคราะห์', accounting:'บัญชี', innovation:'นวัตกรรม',
  salessupport:'Sales Support (จัดซื้อ/วัตถุดิบ)',
  branch:'สาขา (นับสต๊อก/สั่งของ — 1 บัญชีต่อสาขา)',
};
// Display-only mirror of backend ROLE_MODULES (auth.py) for the admin cheatsheet
// so the owner can see what each role unlocks before assigning it. Only a FALLBACK:
// the matrix loads the live (owner-customised) permissions from /api/auth/role-permissions;
// test_role_modules_mirror.py keeps this copy equal to auth.ROLE_MODULES.
const MODULE_LABEL_TH = {
  'sales.overview':'ยอดขายภาพรวม', 'customer.buffet':'บุฟเฟ่ต์/ลูกค้า',
  'menu.analysis':'วิเคราะห์เมนู', 'cost.analysis':'ต้นทุน', 'cost.simulator':'ตัวจำลองต้นทุน',
  'bom.edit':'แก้ไข BoM', 'profit.pnl':'กำไร/P&L', 'ops.report':'รายงานปฏิบัติการ',
  'ops.discounts':'ส่วนลด', 'ops.cash':'เงินขาด/เกิน', 'ops.staff':'พนักงาน',
  'ops.posextra':'POS เชิงลึก', 'report.export':'ส่งออกรายงาน',
  'system.settings':'ตั้งค่า', 'system.admin':'จัดการผู้ใช้', 'data.upload':'อัปโหลดข้อมูล',
  'supply.ordering':'จัดซื้อ/วัตถุดิบ', 'customer.feedback':'เสียงลูกค้า',
  'bom.center':'BoM Center (แก้ไข)', 'bom.release':'BoM Center (ปล่อยเวอร์ชัน)', 'bom.audit':'BoM Center (รับทราบ/ย้อนกลับ)',
  'branch.stock':'สาขา: นับสต๊อก',
};
const ROLE_MODULES_TH = {
  management: Object.keys(MODULE_LABEL_TH),
  marketing:  ['sales.overview','customer.buffet','menu.analysis','report.export','customer.feedback'],
  operation:  ['sales.overview','ops.report','ops.discounts','ops.cash','ops.staff','ops.posextra','report.export','customer.feedback'],
  analyst:    ['sales.overview','customer.buffet','menu.analysis','cost.analysis','cost.simulator','profit.pnl','ops.discounts','ops.posextra','report.export','customer.feedback','bom.center','bom.release'],
  accounting: ['sales.overview','profit.pnl','cost.analysis','ops.cash','ops.discounts','report.export','bom.center'],
  innovation: ['sales.overview','menu.analysis','cost.analysis','cost.simulator','bom.edit','system.settings','customer.feedback','bom.center'],
  salessupport: ['sales.overview','supply.ordering','report.export'],
  branch: ['branch.stock'],
};

function hasModule(m) {
  if (!window.AUTH || !window.AUTH.enabled) return true;   // auth off → everything
  return (window.AUTH.modules || []).includes(m);
}
function tabAllowed(name) {
  const m = TAB_MODULE[name];
  if (Array.isArray(m)) return m.some(hasModule);           // any one of them opens it
  return !m || hasModule(m);                                // unmapped → always allowed
}
function firstAllowedTab() {
  const order = ['overall','home','action','dailysales','trend','compare','more',
    'menu','buffetperf','gpbranch','cost','simhub','opsreport','discounts','cashctl',
    'staff','custtype','ingpurchase','ordering','orderhub','branchstock','manual'];
  return order.find(tabAllowed) || 'manual';
}

async function bootstrapAuth() {
  try {
    const r = await fetch(`${API}/api/auth/me`);
    window.AUTH = await r.json();
    window.AUTH.modules = window.AUTH.modules || [];
  } catch (e) {
    // Network/health failure — don't hard-lock the UI; treat as auth-off.
    window.AUTH = { enabled: false, authenticated: true, role: 'management', modules: [] };
  }
}

function showLogin()  { const o=document.getElementById('loginOverlay'); if(o) o.style.display='flex';
                        setTimeout(()=>document.getElementById('loginUser')?.focus(),50); }
function hideLogin()  { const o=document.getElementById('loginOverlay'); if(o) o.style.display='none'; }

async function doLogin(ev) {
  if (ev) ev.preventDefault();
  const u = document.getElementById('loginUser').value.trim();
  const p = document.getElementById('loginPass').value;
  const errEl = document.getElementById('loginError');
  const btn = document.getElementById('loginBtn');
  errEl.style.display = 'none';
  btn.disabled = true; btn.textContent = 'กำลังเข้าสู่ระบบ…';
  try {
    const res = await fetch(`${API}/api/auth/login`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ username:u, password:p }),
    });
    if (!res.ok) {
      const d = await res.json().catch(()=>({}));
      errEl.textContent = d.detail || 'เข้าสู่ระบบไม่สำเร็จ';
      errEl.style.display = 'block';
      return;
    }
    const data = await res.json();
    window.AUTH = { enabled:true, authenticated:true, username:data.username,
                    role:data.role, modules:data.modules || [] };
    _sessionExpiredShown = false;
    hideLogin();
    applyPermissions();
    // Force a password change off the shared/temporary default before using the app.
    if (data.must_change) { openPasswordChange(true); return; }
    await startApp();
  } catch (e) {
    errEl.textContent = 'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = 'เข้าสู่ระบบ';
  }
}

async function doLogout() {
  try { await fetch(`${API}/api/auth/logout`, { method:'POST' }); } catch(e){}
  location.reload();
}

// ── Change password (forced on first login, or voluntary from the user chip) ──
let _pwForced = false;
function openPasswordChange(force) {
  _pwForced = !!force;
  const ov = document.getElementById('pwOverlay'); if (!ov) return;
  document.getElementById('pwCurrent').value = '';
  document.getElementById('pwNew').value = '';
  document.getElementById('pwConfirm').value = '';
  document.getElementById('pwError').style.display = 'none';
  document.getElementById('pwHint').textContent = force
    ? 'กรุณาตั้งรหัสผ่านใหม่ก่อนใช้งาน (อย่างน้อย 10 ตัวอักษร) — ช่อง "รหัสผ่านปัจจุบัน" ใส่รหัสที่ได้รับมา'
    : 'ตั้งรหัสผ่านใหม่ (อย่างน้อย 10 ตัวอักษร)';
  // When forced, no cancel — the user must set a password before continuing.
  document.getElementById('pwCancel').style.display = force ? 'none' : '';
  ov.style.display = 'flex';
  setTimeout(() => document.getElementById('pwCurrent').focus(), 50);
}
function closePasswordChange() {
  const ov = document.getElementById('pwOverlay'); if (ov) ov.style.display = 'none';
}
async function submitPasswordChange(ev) {
  if (ev) ev.preventDefault();
  const cur = document.getElementById('pwCurrent').value;
  const nw  = document.getElementById('pwNew').value;
  const cf  = document.getElementById('pwConfirm').value;
  const err = document.getElementById('pwError');
  const btn = document.getElementById('pwSubmit');
  const fail = (m) => { err.textContent = m; err.style.display = 'block'; };
  err.style.display = 'none';
  if (nw.length < 10) return fail('รหัสผ่านใหม่ต้องยาวอย่างน้อย 10 ตัวอักษร');   // = auth.MIN_PASSWORD_LEN
  if (nw !== cf)      return fail('รหัสผ่านใหม่ทั้งสองช่องไม่ตรงกัน');
  btn.disabled = true; btn.textContent = 'กำลังบันทึก…';
  try {
    const res = await fetch(`${API}/api/auth/me/password`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ current_password: cur, new_password: nw }),
    });
    if (!res.ok) { const d = await res.json().catch(()=>({})); return fail(d.detail || 'เปลี่ยนรหัสผ่านไม่สำเร็จ'); }
    closePasswordChange();
    if (_pwForced) { _pwForced = false; await startApp(); }   // continue into the app
    else if (typeof notifySuccess === 'function') notifySuccess('เปลี่ยนรหัสผ่านแล้ว');
  } catch (e) {
    fail('เชื่อมต่อเซิร์ฟเวอร์ไม่ได้');
  } finally {
    btn.disabled = false; btn.textContent = 'บันทึกรหัสผ่านใหม่';
  }
}

function renderUserChip() {
  const chip = document.getElementById('userChip');
  if (!chip) return;
  if (window.AUTH && window.AUTH.enabled && window.AUTH.authenticated) {
    const role = window.AUTH.role || '';
    document.getElementById('userChipName').textContent =
      `${window.AUTH.username} · ${ROLE_LABEL_TH[role] || role}`;
    chip.style.display = 'flex';
  } else {
    chip.style.display = 'none';
  }
}

// Hide nav items / sections / topbar actions the current role can't use.
function applyPermissions() {
  renderUserChip();
  if (!window.AUTH || !window.AUTH.enabled) return;   // auth off → show everything
  // 1) Hide disallowed nav items (don't force-show ones intentionally display:none)
  Object.keys(TAB_MODULE).forEach(tab => {
    const el = document.getElementById('nav-' + tab);
    if (el && !tabAllowed(tab)) el.style.display = 'none';
  });
  // 1b) Cluster safety net: each cluster's sidebar entry is tabs[0]; when a
  // role can't open the entry but CAN open other members, reveal the first
  // allowed member's nav item so the cluster stays reachable for that role.
  TAB_CLUSTERS.forEach(cl => {
    if (tabAllowed(cl.tabs[0][0])) return;
    const alt = cl.tabs.find(([t]) => tabAllowed(t));
    if (alt) { const el = document.getElementById('nav-' + alt[0]); if (el) el.style.display = ''; }
  });
  // 2) Collapse nav-section headers whose items are all hidden
  document.querySelectorAll('.nav-section').forEach(sec => {
    let n = sec.nextElementSibling, anyVisible = false;
    while (n && !n.classList.contains('nav-section')) {
      if (n.classList.contains('nav-item') && getComputedStyle(n).display !== 'none') anyVisible = true;
      n = n.nextElementSibling;
    }
    sec.style.display = anyVisible ? '' : 'none';
  });
  // 3) Topbar action buttons
  const costUp = document.querySelector('[onclick="openCostModal()"]');
  if (costUp) costUp.style.display = (hasModule('bom.edit') || hasModule('system.settings')) ? '' : 'none';
  const salesUp = document.querySelector('[onclick*="salesDataFile"]');
  if (salesUp) salesUp.style.display = hasModule('data.upload') ? '' : 'none';
  // The monthly review pack bundles P&L + top cost drivers → cost/profit roles only
  // (server gates /api/export/monthly-pack the same way; this avoids a raw-403 page).
  const packBtn = document.querySelector('[onclick="openMonthlyPackDialog()"]');
  if (packBtn) packBtn.style.display = (hasModule('cost.analysis') || hasModule('profit.pnl')) ? '' : 'none';
  // 4) Settings → ผู้ใช้งาน sub-tab visible to admins only
  const uBtn = document.getElementById('settabbtn-users');
  if (uBtn) uBtn.style.display = hasModule('system.admin') ? '' : 'none';
  // 5) If the current/landing tab isn't allowed, jump to the first allowed one
  if (!tabAllowed(activeTab)) activeTab = firstAllowedTab();
  // 6) The phone summary is the owner's sales view. A role without sales.overview
  //    (the branch login) gets its own pages on the phone instead — they are built 375-px first.
  if (window.AUTH && window.AUTH.enabled && !hasModule('sales.overview')) document.body.classList.add('force-desktop', 'no-mobile-app');
}

// ── User management (Settings → ผู้ใช้งาน) ──────────────────────────────────
async function loadUsers() {
  // Populate the role <select> for the add-user form
  const sel = document.getElementById('newUserRole');
  if (sel && !sel.options.length) {
    Object.entries(ROLE_LABEL_TH).forEach(([k,v]) =>
      sel.add(new Option(`${v} (${k})`, k)));
  }
  const tb = document.getElementById('userTableBody');
  if (tb) tb.innerHTML = '<tr><td class="py-3 text-sm" style="color:var(--text-muted)">กำลังโหลด…</td></tr>';
  // E4: a login can be confined to one branch — the feedback module then shows only that branch
  if (!window._userBranches) {
    try { window._userBranches = ((await api(`${API}/api/branches`)) || []).filter(b => b.active !== false).map(b => b.name); }
    catch (e) { window._userBranches = []; }
    const bs = document.getElementById('newUserBranch');
    if (bs && bs.options.length <= 1) window._userBranches.forEach(b => bs.add(new Option(b, b)));
  }
  const branchOpts = cur => `<option value="">ทุกสาขา</option>` + (window._userBranches || []).map(b => `<option value="${b}" ${b === cur ? 'selected' : ''}>${b}</option>`).join('');
  let users = [];
  try { users = await api(`${API}/api/auth/users`); }
  catch(e) { if (tb) tb.innerHTML = '<tr><td class="py-3 text-sm text-rose-500">โหลดรายชื่อผู้ใช้ไม่ได้</td></tr>'; return; }
  if (!Array.isArray(users)) { if (tb) tb.innerHTML = '<tr><td class="py-3 text-sm text-rose-500">ไม่มีสิทธิ์ดูรายชื่อผู้ใช้</td></tr>'; return; }
  tb.innerHTML = users.map(u => {
    const opts = Object.entries(ROLE_LABEL_TH).map(([k,v]) =>
      `<option value="${k}" ${k===u.role?'selected':''}>${v} (${k})</option>`).join('');
    const me = window.AUTH && window.AUTH.username === u.username;
    return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-2 pr-4 font-medium" style="color:var(--text-primary)">${u.username}${me?' <span class=\"text-xs\" style=\"color:var(--text-muted)\">(คุณ)</span>':''}</td>
      <td class="py-2 pr-4"><select onchange="updateUserRole('${u.username}', this.value)" ${me?'disabled title="เปลี่ยนบทบาทตัวเองไม่ได้"':''} class="border rounded px-2 py-1 text-xs ${me?'opacity-50 cursor-not-allowed':''}" style="border-color:var(--border)">${opts}</select></td>
      <td class="py-2 pr-4"><select onchange="updateUserBranch('${u.username}', this.value)" class="border rounded px-2 py-1 text-xs" style="border-color:var(--border)" title="เสียงลูกค้าจะเห็นเฉพาะสาขานี้">${branchOpts(u.branch || '')}</select></td>
      <td class="py-2 pr-4">${u.active
        ? '<span class="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700">ใช้งาน</span>'
        : '<span class="text-xs px-2 py-0.5 rounded-full bg-slate-200 text-slate-600">ปิด</span>'}</td>
      <td class="py-2 pr-4 text-xs" style="color:var(--text-muted)">${(u.created_at||'').slice(0,10)}</td>
      <td class="py-2 text-right whitespace-nowrap">
        <button onclick="resetUserPassword('${u.username}')" class="text-xs px-2 py-1 rounded border mr-1" style="border-color:var(--border)" title="ตั้งรหัสผ่านใหม่">${ICON.key}</button>
        ${me ? '' : `<button onclick="toggleUserActive('${u.username}', ${u.active?0:1})" class="text-xs px-2 py-1 rounded border mr-1" style="border-color:var(--border)">${u.active?'ปิดใช้':'เปิดใช้'}</button>`}
        ${me ? '' : `<button onclick="deleteUser('${u.username}')" class="text-xs px-2 py-1 rounded border text-rose-600" style="border-color:var(--border)" title="ลบผู้ใช้">${ICON.trash}</button>`}
      </td></tr>`;
  }).join('') || '<tr><td class="py-3 text-sm" style="color:var(--text-muted)">ยังไม่มีผู้ใช้</td></tr>';
}
let _rolePermsData = null;

async function loadRolePermissions() {
  const el = document.getElementById('rolePermMatrix');
  if (!el) return;
  const canEdit = window.AUTH && window.AUTH.enabled && window.AUTH.modules && window.AUTH.modules.includes('system.admin');
  const btn = document.getElementById('permSaveBtn');
  if (btn) btn.classList.toggle('hidden', !canEdit);

  // Try to load from API; fall back to local constants
  let roles, modules, permissions;
  try {
    const d = await fetch(`${API}/api/auth/role-permissions`).then(r => r.ok ? r.json() : null);
    if (d) {
      roles = d.roles;
      modules = d.modules;
      permissions = d.permissions;
    }
  } catch(e) {}
  if (!permissions) {
    roles = Object.keys(ROLE_LABEL_TH);
    modules = Object.keys(MODULE_LABEL_TH);
    permissions = {};
    roles.forEach(r => { permissions[r] = ROLE_MODULES_TH[r] || []; });
  }
  _rolePermsData = { roles, modules, permissions };
  _renderPermMatrix(canEdit);
}

function _renderPermMatrix(canEdit) {
  const el = document.getElementById('rolePermMatrix');
  if (!el || !_rolePermsData) return;
  const { roles, modules, permissions } = _rolePermsData;

  // Group modules for readability
  const MOD_GROUP = {
    'sales.overview':'ยอดขาย', 'customer.buffet':'ลูกค้า', 'menu.analysis':'เมนู',
    'cost.analysis':'ต้นทุน', 'cost.simulator':'Simulator', 'bom.edit':'แก้ BoM',
    'profit.pnl':'กำไร/P&L', 'ops.report':'Ops Report', 'ops.discounts':'ส่วนลด',
    'ops.cash':'เงินขาด/เกิน', 'ops.staff':'พนักงาน', 'ops.posextra':'POS Detail',
    'report.export':'Export', 'system.settings':'Settings', 'system.admin':'Admin',
    'data.upload':'Upload', 'customer.feedback':'เสียงลูกค้า', 'supply.ordering':'จัดซื้อ', 'bom.center':'BoM Center', 'bom.release':'ปล่อยสูตร', 'bom.audit':'รับทราบสูตร',
    'branch.stock':'สาขา: นับสต๊อก',
  };

  const th_role = `<th class="py-2 pr-4 text-left text-xs font-semibold sticky left-0" style="background:var(--bg-subtle);color:var(--text-muted);min-width:120px">บทบาท</th>`;
  const th_mods = modules.map(m =>
    `<th class="py-2 px-2 text-center text-[10px] font-medium whitespace-nowrap" style="color:var(--text-muted)" title="${m}">${MOD_GROUP[m]||m}</th>`
  ).join('');

  const rows = roles.map(role => {
    const roleMods = new Set(permissions[role] || []);
    const cells = modules.map(m => {
      const checked = roleMods.has(m);
      const dis = (role === 'management' && m === 'system.admin') ? ' title="management เสมอ"' : '';
      const inp = canEdit
        ? `<input type="checkbox" data-role="${role}" data-mod="${m}" ${checked?'checked':''} ${dis}
             onchange="_permToggle(this)" class="w-3.5 h-3.5 accent-indigo-600">`
        : `<span style="color:${checked?'#4a70b0':'#e2e8f0'};font-size:14px">${checked?'✓':'–'}</span>`;
      return `<td class="py-1.5 px-2 text-center">${inp}</td>`;
    }).join('');
    return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-4 text-sm font-medium sticky left-0" style="background:var(--bg-card);color:var(--text-primary)">
        ${ROLE_LABEL_TH[role]||role} <span class="text-xs" style="color:var(--text-muted)">(${role})</span>
      </td>${cells}</tr>`;
  }).join('');

  el.innerHTML = `<table class="text-sm w-full border-collapse">
    <thead><tr class="border-b" style="border-color:var(--border)">${th_role}${th_mods}</tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function _permToggle(cb) {
  if (!_rolePermsData) return;
  const role = cb.dataset.role, mod = cb.dataset.mod;
  const perms = _rolePermsData.permissions;
  if (!perms[role]) perms[role] = [];
  if (cb.checked) { if (!perms[role].includes(mod)) perms[role].push(mod); }
  else { perms[role] = perms[role].filter(m => m !== mod); }
}

async function saveRolePermissions() {
  if (!_rolePermsData) return;
  const btn = document.getElementById('permSaveBtn');
  const status = document.getElementById('permSaveStatus');
  if (btn) { btn.disabled = true; btn.textContent = 'กำลังบันทึก…'; }
  try {
    const res = await fetch(`${API}/api/auth/role-permissions`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ permissions: _rolePermsData.permissions }),
    });
    if (!res.ok) {
      const d = await res.json().catch(()=>({}));
      if (status) { status.textContent = d.detail || 'บันทึกไม่สำเร็จ'; status.className = 'text-xs text-rose-600'; }
    } else {
      if (status) { status.textContent = 'บันทึกแล้ว ✓'; status.className = 'text-xs text-emerald-600'; }
      setTimeout(() => { if (status) status.textContent = ''; }, 3000);
      await loadRolePermissions();
    }
  } catch(e) {
    if (status) { status.textContent = 'เชื่อมต่อไม่ได้'; status.className = 'text-xs text-rose-600'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'บันทึกสิทธิ์'; }
  }
}

function _userStatus(msg, ok, ms) {
  const s = document.getElementById('userSaveStatus');
  if (!s) return;
  s.textContent = msg; s.className = 'text-sm ' + (ok ? 'text-emerald-600' : 'text-rose-600');
  setTimeout(() => { s.textContent = ''; }, ms || 4000);
}
async function createUser() {
  const username = document.getElementById('newUserName').value.trim();
  const password = document.getElementById('newUserPass').value;
  const role = document.getElementById('newUserRole').value;
  const branch = (document.getElementById('newUserBranch') || {}).value || '';
  if (!username) { _userStatus('กรอกชื่อผู้ใช้', false); return; }
  const res = await fetch(`${API}/api/auth/users`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ username, password, role, branch }) });
  if (res.ok) {
    document.getElementById('newUserName').value = '';
    document.getElementById('newUserPass').value = '';
    const d = await res.json().catch(()=>({}));
    // Blank password → server used the shared default; say so, the admin has to pass it on.
    _userStatus(d.default_password
      ? `เพิ่ม ${username} แล้ว — รหัสเริ่มต้น ${d.default_password} (ระบบบังคับเปลี่ยนตอนเข้าครั้งแรก)`
      : 'เพิ่มผู้ใช้แล้ว', true, 12000);
    loadUsers();
  } else {
    const d = await res.json().catch(()=>({})); _userStatus(d.detail || 'เพิ่มไม่สำเร็จ', false);
  }
}
async function _putUser(username, body, okMsg) {
  const res = await fetch(`${API}/api/auth/users/${encodeURIComponent(username)}`, {
    method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (res.ok) { _userStatus(okMsg, true); loadUsers(); }
  else { const d = await res.json().catch(()=>({})); _userStatus(d.detail || 'บันทึกไม่สำเร็จ', false); }
}
function updateUserRole(username, role) { _putUser(username, { role }, `เปลี่ยนบทบาท ${username} แล้ว`); }
function updateUserBranch(username, branch) { _putUser(username, { branch }, branch ? `${username} เห็นเฉพาะสาขา ${branch}` : `${username} เห็นทุกสาขา`); }
function toggleUserActive(username, active) { _putUser(username, { active: !!active }, 'อัปเดตสถานะแล้ว'); }
function resetUserPassword(username) {
  const pw = window.prompt(`ตั้งรหัสผ่านใหม่สำหรับ ${username}
(เว้นว่างแล้วกด OK = กลับเป็นรหัสเริ่มต้น Oshinei@2026 และบังคับเปลี่ยนตอนเข้าครั้งถัดไป):`);
  if (pw === null) return;                                  // cancelled
  if (pw) _putUser(username, { password: pw }, 'ตั้งรหัสผ่านใหม่แล้ว');
  else _putUser(username, { reset_default: true }, `${username} กลับเป็นรหัสเริ่มต้น Oshinei@2026 แล้ว — ต้องเปลี่ยนตอนเข้าครั้งถัดไป`);
}
async function deleteUser(username) {
  if (!window.confirm(`ลบผู้ใช้ ${username}?`)) return;
  const res = await fetch(`${API}/api/auth/users/${encodeURIComponent(username)}`, { method:'DELETE' });
  if (res.ok) { _userStatus('ลบผู้ใช้แล้ว', true); loadUsers(); }
  else { const d = await res.json().catch(()=>({})); _userStatus(d.detail || 'ลบไม่สำเร็จ', false); }
}

// Formal theme: muted, deep hues that each reach 3:1 on BOTH the light and the dark card,
// so one palette serves both themes (design-system/tokens.json chart-1..12).
// Categorical series colours (owner 2026-10-01: the muted set read as dull). The
// first eight are the dataviz reference palette — adjacent pairs pass the CVD
// (deutan/protan/tritan) and normal-vision separation gates on a light surface —
// followed by four spares for the rare chart with more than eight series.
// 2026-10-02 owner: "สีแท่งสดไปหน่อย … อยากได้ความพาสเทล" — the pastel is applied to EVERY
// bar by the glance layer's shade (see _glanceGrad: tip 25% / base 50% toward the
// surface), so status-coloured bars (good/bad amber-green-red) soften with the
// series ones. The palette itself stays the validated reference set; legends and
// tooltips keep these true colours.
const PALETTE = ['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#4a3aa7','#e34948','#1d9bd8','#c47b2a','#9b5fb8','#5a9f33'];

// ── World-class semantic color system ─────────────────────────
const SEMANTIC = {
  revenue:    '#4a70b0',  // indigo
  customers:  '#10b981',  // emerald
  avgTicket:  '#06b6d4',  // cyan
  margin:     '#14b8a6',  // teal
  cost:       '#f97316',  // orange
  discount:   '#f59e0b',  // amber
  refund:     '#ef4444',  // red
  good:       '#10b981',
  bad:        '#ef4444',
  neutral:    '#64748b',
  // Daypart colors (restaurant timeline)
  morning:    '#f59e0b',
  lunch:      '#10b981',
  afternoon:  '#fbbf24',
  dinner:     '#4a70b0',
  late:       '#7a66b8',
  // Branch group
  master:     '#4a70b0',
  franchise:  '#f59e0b',
  easy:       '#4a70b0',
};

// Thai day-of-week color palette
const DOW_COLORS = {
  0: '#ef4444',  // Sun = red
  1: '#eab308',  // Mon = yellow
  2: '#ec4899',  // Tue = pink
  3: '#10b981',  // Wed = green
  4: '#f97316',  // Thu = orange
  5: '#3b82f6',  // Fri = blue
  6: '#7a66b8',  // Sat = purple
};
const DOW_LABELS_TH_SHORT = ['จ','อ','พ','พฤ','ศ','ส','อา'];
const DOW_LABELS_TH = ['อาทิตย์','จันทร์','อังคาร','พุธ','พฤหัสบดี','ศุกร์','เสาร์'];

// Deterministic, category-specific palette (anchored to common Thai-restaurant categories)
const CATEGORY_COLOR_MAP = {
  'BUFFET': '#4a70b0', 'BUFFET 350': '#1f3a6b', 'BUFFET 450': '#4a70b0',
  'A LA CARTE': '#10b981', 'FOOD': '#10b981',
  'DRINK': '#06b6d4', 'BEVERAGE': '#06b6d4', 'SOFT DRINK': '#0891b2',
  'ALCOHOL': '#9333ea', 'BEER': '#a855f7', 'WINE': '#7a66b8',
  'DESSERT': '#ec4899', 'SWEETS': '#f472b6',
  'APPETIZER': '#f59e0b', 'SIDE': '#fbbf24',
  'YAKINIKU': '#dc2626', 'BBQ': '#ef4444', 'GRILL': '#dc2626',
  'SUSHI': '#0d9488', 'SASHIMI': '#14b8a6',
  'NOODLE': '#f97316', 'RAMEN': '#ea580c', 'PASTA': '#fb923c',
  'RICE': '#84cc16', 'CURRY': '#fbbf24',
  'SET MENU': '#7a66b8',
  'OTHER': '#64748b', 'OTHERS': '#94a3b8',
  'SERVICE': '#94a3b8', 'SERVICECHARGE': '#94a3b8', 'SP': '#94a3b8',
};
// Hash-based fallback so unknown categories get a stable color
function _hashColor(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}
function colorForCategory(name) {
  if (!name) return SEMANTIC.neutral;
  const up = name.toUpperCase().trim();
  // exact match
  if (CATEGORY_COLOR_MAP[up]) return CATEGORY_COLOR_MAP[up];
  // partial — look for any known token in name
  for (const k in CATEGORY_COLOR_MAP) {
    if (up.includes(k)) return CATEGORY_COLOR_MAP[k];
  }
  return _hashColor(up);
}
function colorForHour(h) {
  if (h >= 6  && h <= 10) return SEMANTIC.morning;
  if (h >= 11 && h <= 14) return SEMANTIC.lunch;
  if (h >= 15 && h <= 16) return SEMANTIC.afternoon;
  if (h >= 17 && h <= 21) return SEMANTIC.dinner;
  return SEMANTIC.late;
}
// Rank-tier colors (top tier emerald → middle indigo → bottom amber)
function colorForRank(idx, total) {
  const t = idx / Math.max(1, total - 1);
  if (t < 0.33) return SEMANTIC.good;        // top third
  if (t < 0.66) return SEMANTIC.revenue;     // middle third
  return SEMANTIC.discount;                  // bottom third
}
// ── Heat scales (heatmaps) ──
// The old red→amber→green scale painted VOLUME as good/bad: a small branch or a
// quiet hour came out "red" though nothing was wrong. Two scales instead:
//  - sequential = "how much": one navy hue, light → dark, in 5 steps so a cell
//    reads as a band rather than a shade to squint at;
//  - diverging  = "vs normal": red below, grey around, green above - only where
//    a baseline makes good/bad true (a cell vs its own branch's average).
// The fills are theme TOKENS (--heat-s1..5 / --heat-d1..5 with matching -ink
// label colours, index.html), so a heatmap follows the theme toggle without a
// re-render; inkOn() maps a token fill to its ink token.
// % vs normal edges: <=-20 | -20..-5 | ±5 | 5..20 | >=20
// t = value / max, in (0, 1]
function heatSeq(t) {
  if (t == null || !isFinite(t) || t <= 0) return null;
  return `var(--heat-s${Math.min(4, Math.floor(Math.min(1, t) * 5 - 1e-9)) + 1})`;
}
function heatDiv(pct) {
  if (pct == null || !isFinite(pct)) return null;
  return `var(--heat-d${(pct <= -20 ? 0 : pct < -5 ? 1 : pct <= 5 ? 2 : pct < 20 ? 3 : 4) + 1})`;
}
// A legend row: 5 swatches with their labels.
function heatLegend(kind, labels) {
  const cols = [1, 2, 3, 4, 5].map(i => `var(--heat-${kind === 'div' ? 'd' : 's'}${i})`);
  return `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:10.5px;color:var(--text-muted)">${cols.map((c, i) =>
    `<span style="display:inline-flex;align-items:center;gap:4px"><span style="width:14px;height:10px;border-radius:2px;background:${c};border:1px solid var(--border)"></span>${labels[i] || ''}</span>`).join('')}</div>`;
}
// Label colour for text ON a data-driven fill (heat cells, bar segments, badges): white or
// the light-theme primary ink, whichever contrasts more. Fixed hex both themes, like the fill.
// White fails on the whole heat scale (#ef4444 3.76, #f59e0b 2.15, #16a34a 3.30); #0f172a clears it.
function inkOn(bg) {
  const tok = String(bg || '').match(/^var\(--heat-([sd][1-5])\)$/);
  if (tok) return `var(--heat-${tok[1]}-ink)`;   // heat fills are theme tokens with their own ink
  const m = String(bg || '').trim().match(/^#(?:([0-9a-f]{6})(?:[0-9a-f]{2})?|([0-9a-f]{3}))(?![0-9a-f])|^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i);
  if (!m) return '#fff';
  let rgb;
  if (m[1] || m[2]) { const h = m[1] || m[2].replace(/./g, c => c + c); rgb = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)); }
  else rgb = [m[3], m[4], m[5]].map(Number);
  const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const L = 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
  const onWhite = 1.05 / (L + 0.05), onNavy = (L + 0.05) / (0.0093 + 0.05);   // #0f172a L = 0.0093
  return onWhite >= onNavy ? '#fff' : '#0f172a';
}
// Deterministic, distinct colour per branch name (stable across reloads)
function colorForBranch(name) {
  if (!name) return SEMANTIC.neutral;
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

// Theme-aware axis / grid colors that respect dark mode
function chartTheme(force) {
  // force='light' → a chart inside a forced-light island (e.g. the printable
  // Management Report keeps a white-paper look in dark mode) themes as light.
  const dark = force ? force === 'dark' : document.body.classList.contains('dark');
  return {
    text:     dark ? '#cbd5e1' : '#64748b',
    textDim:  dark ? '#94a3b8' : '#94a3b8',
    grid:     dark ? 'rgba(148,163,184,0.12)' : '#f1f5f9',
    labelStrong: dark ? '#f1f5f9' : '#1e293b',
    bg:       dark ? '#1e293b' : '#ffffff',
    // Legend / title ink. Chart.defaults.color is a fixed #475569, which is
    // 2.3:1 on the dark card, so each chart gets a theme-aware default.
    ink:      dark ? '#94a3b8' : '#475569',
  };
}
const _CHART_SLATE = ['#64748b', '#94a3b8', '#334155', '#475569'];
function applyChartTheme(cfg, force) {
  const t = chartTheme(force);
  if (!cfg.options) return;
  if (cfg.options.color === undefined) cfg.options.color = t.ink;
  if (cfg.options.scales) {
    Object.values(cfg.options.scales).forEach(ax => {
      if (!ax) return;
      ax.grid = ax.grid || {};
      ax.ticks = ax.ticks || {};
      if (ax.grid.color !== false && ax.grid.display !== false) ax.grid.color = t.grid;
      ax.ticks.color = ax.ticks.color && /^#[0-9a-fA-F]{6}$/.test(ax.ticks.color) ? t.text : ax.ticks.color || t.text;
      // overwrite hard-coded slate colors
      if (ax.ticks.color === '#94a3b8' || ax.ticks.color === '#64748b') ax.ticks.color = t.text;
      if (ax.title && _CHART_SLATE.includes(ax.title.color)) ax.title.color = t.text;
    });
  }
  // Legend / title text hard-coded to a light-mode slate: #64748b is 3.65:1 on
  // the dark card, #334155 1.8:1, and #94a3b8 is 2.56:1 on white. Theme ink instead.
  const lab = cfg.options.plugins?.legend?.labels;
  if (lab && _CHART_SLATE.includes(lab.color)) lab.color = t.ink;
  const ttl = cfg.options.plugins?.title;
  if (ttl && _CHART_SLATE.includes(ttl.color)) ttl.color = t.ink;
  if (cfg.options.plugins?.datalabels?.color === '#1e293b') {
    cfg.options.plugins.datalabels.color = t.labelStrong;
  }
}
// ── At-a-glance layer (every chart) ───────────────────────────────────────────
// The formal theme (#223) left every chart a flat block the reader had to hover
// to read. This one plugin, registered globally in ensureChartSetup(), puts the
// answer back on the chart with no per-chart code:
//   depth   bars + filled line areas get a gradient along the value axis (strong
//           at the tip, fading to the baseline), and a lone solid line gets a wash
//   labels  the latest value at the end of each line (<= 4 lines) and the peak of
//           the primary series; time-series bars also label the latest bar
//   avg     a hairline at the mean of a single primary series (>= 4 points)
//   centre  a doughnut shows its total (or its top share, when values are %)
//   headline one sentence above the chart: latest vs previous, highest/lowest
//           since, a run of rises/falls, vs the average (or best/worst + spread
//           for a category chart). Skipped when the card already states numbers.
//   MTD     the period still in progress (this month on a monthly axis, today on
//           a daily one) is drawn hollow/dashed, tagged MTD, and left out of the
//           peak, the average and every headline comparison.
//   weekend Sat-Sun bands behind a daily axis.
// Dates come from the x labels (2026-09-24, 09-24, Sep 2026, ก.ย. 69, ...) or
// from options.plugins.glance.dates (ISO strings) when the labels are bare days.
// A dataset the datalabels plugin already numbers is never labelled again.
// Opt out per chart with options.plugins.glance = false, or one part with
// glance: { depth:false, labels:false, avg:false, centre:false, headline:false,
// mtd:false, weekend:false }.
const _GLANCE_TIME_RE = /^\d{4}(-\d{2})?|^\d{1,2}[-\/]\d{1,2}|^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|^(ม\.?ค|ก\.?พ|มี\.?ค|เม\.?ย|พ\.?ค|มิ\.?ย|ก\.?ค|ส\.?ค|ก\.?ย|ต\.?ค|พ\.?ย|ธ\.?ค)|^(w|wk)\s?\d|^สัปดาห์|^q[1-4]/i;
const _GLANCE_SKIP_RE = /band|\blo\b|\bhi\b|bound|range|ช่วง/i;
const _GLANCE_REF_RE  = /เฉลี่ย|avg|average|mean|เป้า|target/i;

function _glanceRgba(c) {
  if (typeof c !== 'string') return null;
  let m = c.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})([0-9a-f]{2})?$/i);
  if (m) {
    let h = m[1];
    if (h.length === 3) h = h.split('').map(x => x + x).join('');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16),
            m[2] ? parseInt(m[2], 16) / 255 : 1];
  }
  m = c.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,\/]+([\d.]+%?))?\s*\)$/i);
  if (m) return [+m[1], +m[2], +m[3], m[4] == null ? 1 : (m[4].endsWith('%') ? parseFloat(m[4]) / 100 : +m[4])];
  return null;
}
const _glanceCss = (r, a) => `rgba(${r[0]},${r[1]},${r[2]},${Math.max(0, Math.min(1, a)).toFixed(3)})`;

function _glanceCompact(v) {
  const a = Math.abs(v), s = v < 0 ? '-' : '';
  if (a >= 1e6) return s + (a / 1e6).toFixed(2) + 'M';
  if (a >= 1e4) return s + (a / 1e3).toFixed(1) + 'K';
  if (a >= 100) return s + Math.round(a).toLocaleString('en-US');
  if (a >= 1 || a === 0) return s + parseFloat(a.toFixed(1));
  return s + parseFloat(a.toFixed(2));
}

// A label in the axis's own unit: '%' and a '฿' prefix are read off the scale's
// tick callback; the number itself is always the compact form (a tick callback
// may round to the axis step, which is too coarse for the one value we print).
function _glanceFmt(scale, v) {
  let pre = '', suf = '';
  const cb = scale && scale.options && scale.options.ticks && scale.options.ticks.callback;
  if (typeof cb === 'function') {
    try {
      const s = String(cb.call(scale, v, 0, [{ value: v }]) ?? '');
      if (/%\s*$/.test(s)) suf = '%';
      if (/^\s*฿/.test(s)) pre = '฿';
    } catch (e) { /* keep the bare number */ }
  }
  return pre + _glanceCompact(v) + suf;
}

function _glanceTheme(chart) {
  const body = document.body.className;
  const g = chart.$glance || (chart.$glance = {});
  if (g.key !== body) {
    g.key = body;
    g.theme = chartTheme(chart.canvas.closest('[data-force-light]') ? 'light' : undefined);
    g.surface = g.theme.bg;
    for (let el = chart.canvas.parentElement; el && el !== document.documentElement; el = el.parentElement) {
      const bg = getComputedStyle(el).backgroundColor;
      const rgba = _glanceRgba(bg);
      if (rgba && rgba[3] > 0.9) { g.surface = bg; break; }
    }
  }
  return g;
}

function _glanceOpt(chart, part) {
  const o = chart.options.plugins && chart.options.plugins.glance;
  return !(o && o[part] === false);
}

function _glanceLabelled(chart, ds) {
  if (!Chart.registry.plugins.get('datalabels')) return false;
  if (ds.datalabels === false) return false;
  let d = ds.datalabels && ds.datalabels.display;
  if (d === undefined) {
    const po = chart.config.options && chart.config.options.plugins && chart.config.options.plugins.datalabels;
    if (po === false) return false;
    d = po && po.display;
  }
  if (d === undefined) d = Chart.defaults.plugins.datalabels && Chart.defaults.plugins.datalabels.display;
  return d !== false;
}

const _glanceIsLine = (chart, ds) => (ds.type || chart.config.type) === 'line';
const _glanceIsBar  = (chart, ds) => (ds.type || chart.config.type) === 'bar';
const _glanceFinite = v => {
  const n = (v && typeof v === 'object') ? v.y : v;
  return (typeof n === 'number' && isFinite(n)) ? n : null;
};

// Which visible datasets get labels, and which one is "the" series (peak + avg).
// `loose` (the headline) also takes small and horizontal charts.
function _glancePlan(chart, loose) {
  const type = chart.config.type;
  if (type !== 'line' && type !== 'bar') return null;
  const horizontal = chart.options.indexAxis === 'y';
  const area = chart.chartArea;
  if (!loose && (!area || area.bottom - area.top < 90)) return null;   // sparklines: depth only
  const metas = chart.getSortedVisibleDatasetMetas();
  const stacked = metas.some(m => m.vScale && m.vScale.options && m.vScale.options.stacked);
  if (stacked || (horizontal && !loose)) return null;
  const lines = [], bars = [];
  metas.forEach(m => {
    const ds = chart.data.datasets[m.index];
    if (!ds || _GLANCE_SKIP_RE.test(ds.label || '')) return;
    if (!(ds.data || []).some(v => _glanceFinite(v))) return;   // an empty / all-zero overlay is not a series
    if (m.type === 'line' && (ds.borderWidth == null || ds.borderWidth >= 1)) lines.push(m);
    else if (m.type === 'bar') bars.push(m);
  });
  let primary = null;
  if (bars.length === 1) primary = bars[0];
  else if (!bars.length) {
    const solid = lines.filter(m => !(chart.data.datasets[m.index].borderDash || []).length);
    if (solid.length === 1) primary = solid[0];
  }
  const labels = (chart.data.labels || []).map(l => Array.isArray(l) ? l.join(' ') : String(l ?? ''));
  const timeLike = !horizontal && labels.length > 1 && (labels.filter(l => _GLANCE_TIME_RE.test(l.trim())).length >= labels.length * 0.6
                                          || labels.every(l => /^\d{1,2}$/.test(l.trim())));   // day-of-month axis
  return { lines: lines.length <= 4 ? lines : [], primary, timeLike };
}

function _glanceText(ctx, text, x, y, align, color, surface, size, weight) {
  ctx.font = `${weight} ${size}px ${Chart.defaults.font.family}`;
  ctx.textAlign = align;
  ctx.textBaseline = 'middle';
  if (surface) {   // a surface-coloured halo keeps the text clear of grid + lines
    ctx.lineJoin = 'round';
    ctx.lineWidth = 3;
    ctx.strokeStyle = surface;
    ctx.strokeText(text, x, y);
  }
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}

function _glanceDot(ctx, x, y, color, surface) {
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = surface;
  ctx.stroke();
}

// Bar fill, cached per chart area + colour. Two rules make bars read as solid,
// even colour instead of milky:
//  - a see-through colour (many charts fade their non-highlighted bars) is
//    flattened onto the card surface first, so it keeps its tone but gridlines
//    no longer show through it;
//  - the gradient is a SHADE, not a fade: full colour at the tip easing to the
//    colour mixed 50% toward the surface at the zero line (both ways when
//    negatives exist), so a short bar is still clearly its colour.
const _glanceMix = (c, t, k) => [0, 1, 2].map(i => Math.round(c[i] + (t[i] - c[i]) * k)).concat(1);
// Lift a colour in HSL: +dl lightness (clamped to 0.86 so it never turns white) and
// saturation × ks (clamped to 1) — the pastel that keeps its hue.
function _glanceLift(c, dl, ks) {
  const r = c[0] / 255, g = c[1] / 255, b = c[2] / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
  let h = 0, sat = 0, l = (mx + mn) / 2;
  if (mx !== mn) {
    const d = mx - mn;
    sat = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    h = mx === r ? ((g - b) / d + (g < b ? 6 : 0)) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h /= 6;
  }
  l = Math.min(0.86, l + dl); sat = Math.min(1, sat * ks);
  const f = (p, q, t) => { t = (t + 1) % 1; return t < 1 / 6 ? p + (q - p) * 6 * t : t < 1 / 2 ? q : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6 : p; };
  if (sat === 0) { const v = Math.round(l * 255); return [v, v, v, 1]; }
  const q = l < 0.5 ? l * (1 + sat) : l + sat - l * sat, pp = 2 * l - q;
  return [Math.round(f(pp, q, h + 1 / 3) * 255), Math.round(f(pp, q, h) * 255), Math.round(f(pp, q, h - 1 / 3) * 255), 1];
}
function _glanceGrad(chart, rgba, surf, vs, horizontal) {
  const a = chart.chartArea, g = chart.$glance || (chart.$glance = {});
  const zero = vs && typeof vs.getPixelForValue === 'function' ? vs.getPixelForValue(0) : (horizontal ? a.left : a.bottom);
  const key = `${rgba.join()}|${surf.join()}|${horizontal}|${a.left}|${a.top}|${a.right}|${a.bottom}|${Math.round(zero)}`;
  g.grads = g.grads || new Map();
  if (g.grads.has(key)) return g.grads.get(key);
  const solid = rgba[3] >= 0.999 ? rgba : _glanceMix(rgba, surf, 1 - rgba[3]);
  // Pastel bars (owner 2026-10-02, "พาสเทลได้แล้ว แต่อยากให้สีเด่นขึ้น"): lighter, not
  // washed — the hue keeps (even gains) its saturation while lightness rises, so
  // a bar reads as a clear candy colour instead of a grey tint. Tip = L+0.10,
  // S×1.25; base = L+0.24, S×1.15 (clamped). Applies to every bar chart whatever
  // colour it was given; legends/tooltips keep the true colour.
  const strong = _glanceCss(_glanceLift(solid, 0.11, 1.10), 1), weak = _glanceCss(_glanceLift(solid, 0.25, 1.05), 1);
  const ctx = chart.ctx;
  const grad = horizontal ? ctx.createLinearGradient(a.left, 0, a.right, 0)
                          : ctx.createLinearGradient(0, a.top, 0, a.bottom);
  const span = horizontal ? a.right - a.left : a.bottom - a.top;
  const z = Math.max(0, Math.min(1, ((horizontal ? zero - a.left : zero - a.top) / (span || 1))));
  // Strong at both far edges, lightest at the zero line (a baseline at an edge
  // simply drops that edge's stop, so the bar never ends in a hard 1px band).
  if (z > 0.001) grad.addColorStop(0, strong);
  grad.addColorStop(z, weak);
  if (z < 0.999) grad.addColorStop(1, strong);
  if (g.grads.size > 64) g.grads.clear();
  g.grads.set(key, grad);
  return grad;
}

// ── Dates behind the x labels ──
const _GLANCE_EN_MON = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
const _GLANCE_TH_MON = ['ม.ค','ก.พ','มี.ค','เม.ย','พ.ค','มิ.ย','ก.ค','ส.ค','ก.ย','ต.ค','พ.ย','ธ.ค'];
const _glanceCfg = chart => (chart.config.options && chart.config.options.plugins && chart.config.options.plugins.glance) || {};

// The last day with data (set by initDataCoverageNote); until it arrives, yesterday.
function _glanceLastDate() {
  const s = window.__dataLastDate;
  if (typeof s === 'string' && /^\d{4}-\d{2}-\d{2}/.test(s)) {
    const [y, m, d] = s.slice(0, 10).split('-').map(Number);
    return { y, m, d };
  }
  const t = new Date(Date.now() + 7 * 3600e3 - 864e5);   // Bangkok yesterday
  return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() };
}
// 2-digit years: <= 50 is 20xx (CE), above is 25xx (BE, as in "ก.ย. 69").
function _glanceYear(s) { const n = +s; return s.length <= 2 ? (n > 50 ? 2500 + n - 543 : 2000 + n) : (n > 2400 ? n - 543 : n); }
function _glanceParseLabel(l, ref) {
  l = l.trim();
  let m, mo;
  if ((m = l.match(/^(\d{4})-(\d{2})-(\d{2})/))) return { y: +m[1], m: +m[2], d: +m[3] };
  if ((m = l.match(/^(\d{4})-(\d{2})$/))) return { y: +m[1], m: +m[2], d: null };
  if ((m = l.match(/^(\d{2})-(\d{2})$/))) { mo = +m[1]; return { y: mo > ref.m ? ref.y - 1 : ref.y, m: mo, d: +m[2] }; }
  if ((m = l.match(/^(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{2,4})$/)) && (mo = _GLANCE_EN_MON.indexOf(m[2].toLowerCase()) + 1))
    return { y: _glanceYear(m[3]), m: mo, d: +m[1] };
  if ((m = l.match(/^([A-Za-z]{3})[a-z]*\.?\s+(\d{2,4})$/)) && (mo = _GLANCE_EN_MON.indexOf(m[1].toLowerCase()) + 1))
    return { y: _glanceYear(m[2]), m: mo, d: null };
  if ((m = l.match(/^(?:(\d{1,2})\s*)?([ก-๙]{1,3}\.[ก-๙]{1,2})\.?\s*(\d{2,4})?$/)) && (mo = _GLANCE_TH_MON.indexOf(m[2]) + 1))
    return { y: m[3] ? _glanceYear(m[3]) : (mo > ref.m ? ref.y - 1 : ref.y), m: mo, d: m[1] ? +m[1] : null };
  return null;
}

// { grain:'day'|'month'|null, dates, partial: index of the period still in progress (-1), note }
function _glanceInfo(chart) {
  return glancePeriods((chart.config.data && chart.config.data.labels) || [], _glanceCfg(chart));
}
// The same, for any list of period labels (heatmaps, tables): which one is still in progress.
function glancePeriods(labels, go) {
  go = go || {};
  const none = { grain: null, dates: null, partial: -1, note: '' };
  if (labels.length < 2) return none;
  const ref = _glanceLastDate();
  let dates = null;
  if (Array.isArray(go.dates) && go.dates.length === labels.length) dates = go.dates.map(x => _glanceParseLabel(String(x), ref));
  if (!dates || !dates.every(Boolean)) dates = labels.map(l => _glanceParseLabel(Array.isArray(l) ? l.join(' ') : String(l ?? ''), ref));
  if (!dates.every(Boolean)) return none;
  const grain = dates.every(d => d.d != null) ? 'day' : dates.every(d => d.d == null) ? 'month' : null;
  let partial = -1, note = '';
  if (grain === 'month') {
    const dim = new Date(ref.y, ref.m, 0).getDate();
    const i = dates.findIndex(d => d.y === ref.y && d.m === ref.m);
    if (i >= 0 && ref.d < dim) { partial = i; note = `${ref.d}/${dim} วัน`; }
  } else if (grain === 'day') {
    const t = new Date(Date.now() + 7 * 3600e3);   // a day still trading = today, Bangkok
    partial = dates.findIndex(d => d.y === t.getUTCFullYear() && d.m === t.getUTCMonth() + 1 && d.d === t.getUTCDate());
  }
  if (go.mtd === false) partial = -1;
  return { grain, dates, partial, note };
}
const _glancePartial = chart => (chart.$gi && chart.$gi.partial >= 0) ? chart.$gi.partial : -1;
const _glanceActual = ds => !(ds.borderDash || []).length && !_GLANCE_REF_RE.test(ds.label || '');
const _glanceTag = chart => (chart.$gi && chart.$gi.grain === 'day') ? ' วันนี้' : ' MTD';

// ── Headline (one sentence above the chart) ──
const _glanceLab = (chart, i) => {
  const l = (chart.data.labels || [])[i];
  let s = Array.isArray(l) ? l.join(' ') : String(l ?? '');
  const d = chart.$gi && chart.$gi.dates && chart.$gi.dates[i];
  if (d && d.d != null && /^\d{1,2}$/.test(s.trim())) s = `${d.d} ${_GLANCE_TH_MON[d.m - 1]}.`;
  return s.length > 24 ? s.slice(0, 23) + '…' : s;
};
const _glanceB = t => `<b style="color:var(--text-primary);font-weight:600">${t}</b>`;

// The sentence itself, shared by the chart headlines and the phone views
// (js/09c-mobile.js). labels[i] are display strings, vals[i] numbers or null.
// o: { partial: index still in progress (-1), unit: 'เดือน'|'วัน'|'ช่วง', note,
//      pct: values are percentages (compare in pp), fmt: v => string }
function glanceTrendSentence(labels, vals, o) {
  const P = o.partial == null ? -1 : o.partial, f = v => esc(o.fmt(v)), lab = i => esc(labels[i] ?? '');
  const unit = o.unit || 'ช่วง', pct = !!o.pct;
  const idx = vals.map((v, i) => i).filter(i => vals[i] != null && i !== P);
  if (idx.length < 3) return '';
  const parts = [];
  const L = idx[idx.length - 1], Pv = idx[idx.length - 2], v = vals[L], pv = vals[Pv];
  parts.push(_glanceB(`${lab(L)} ${f(v)}`));
  if (pct) {
    const d = v - pv;
    parts.push(`${d >= 0 ? '▲' : '▼'} ${Math.abs(d).toFixed(1)} pp จาก ${lab(Pv)}`);
  } else if (pv > 0) {
    const d = (v / pv - 1) * 100;
    parts.push(`${d >= 0 ? '▲' : '▼'} ${Math.abs(d).toFixed(1)}% จาก ${lab(Pv)}`);
  }
  const cv = idx.map(i => vals[i]), hi = Math.max(...cv), lo = Math.min(...cv);
  if (v === hi) parts.push(_glanceB('สูงสุดในช่วงที่แสดง'));
  else if (v === lo) parts.push(_glanceB('ต่ำสุดในช่วงที่แสดง'));
  else {
    // "highest since X": the last earlier point at least as high, if >= 3 periods back
    const k = idx.length - 1;
    for (let j = k - 1; j >= 0; j--) {
      const w = vals[idx[j]];
      if (v > pv ? w >= v : w <= v) {
        if (k - j >= 3) parts.push(`${v > pv ? 'สูงสุด' : 'ต่ำสุด'}ตั้งแต่ ${lab(idx[j])}`);
        break;
      }
    }
  }
  let run = 0;
  for (let j = idx.length - 1; j > 0 && v !== pv && Math.sign(vals[idx[j]] - vals[idx[j - 1]]) === Math.sign(v - pv); j--) run++;
  if (run >= 3) parts.push(`${v > pv ? 'เพิ่มขึ้น' : 'ลดลง'} ${run} ${unit}ติดกัน`);
  if (idx.length >= 4 && parts.length < 4) {
    const avg = cv.reduce((a, b) => a + b, 0) / cv.length;
    if (pct && Math.abs(v - avg) >= 0.5) parts.push(`${v > avg ? 'สูงกว่า' : 'ต่ำกว่า'}ค่าเฉลี่ย ${Math.abs(v - avg).toFixed(1)} pp`);
    else if (!pct && avg > 0 && Math.abs(v / avg - 1) >= 0.05) parts.push(`${v > avg ? 'สูงกว่า' : 'ต่ำกว่า'}ค่าเฉลี่ย ${Math.abs((v / avg - 1) * 100).toFixed(0)}%`);
    else if (!pct && avg > 0) parts.push('ใกล้ค่าเฉลี่ย');
  }
  parts.splice(4);
  if (P >= 0 && vals[P] != null) {
    parts.push(`<span style="color:var(--text-muted)">${lab(P)} ยังไม่ครบ${unit}${o.note ? ` (${esc(o.note)})` : ''} · ไม่นำมาเทียบ</span>`);
  }
  return parts.join(' · ');
}

// Best / worst + spread for a category list (branches, dayparts, menu items).
// o: { pct, fmt, below: true adds "n of N below the average" }
function glanceRangeSentence(labels, vals, o) {
  const f = v => esc(o.fmt(v)), lab = i => esc(labels[i] ?? '');
  const idx = vals.map((v, i) => i).filter(i => vals[i] != null);
  if (idx.length < 3) return '';
  let hi = idx[0], lo = idx[0];
  idx.forEach(i => { if (vals[i] > vals[hi]) hi = i; if (vals[i] < vals[lo]) lo = i; });
  if (vals[hi] === vals[lo]) return '';
  const parts = [`สูงสุด ${_glanceB(`${lab(hi)} ${f(vals[hi])}`)}`, `ต่ำสุด ${_glanceB(`${lab(lo)} ${f(vals[lo])}`)}`];
  if (o.pct) parts.push(`ห่างกัน ${(vals[hi] - vals[lo]).toFixed(1)} pp`);
  // a ratio reads only in a sane range ("1913 เท่า" against a near-empty bucket says nothing)
  else if (vals[lo] > 0 && vals[hi] / vals[lo] >= 1.5 && vals[hi] / vals[lo] <= 20) parts.push(`ต่างกัน ${(vals[hi] / vals[lo]).toFixed(1)} เท่า`);
  if (o.below) {
    const avg = idx.reduce((a, i) => a + vals[i], 0) / idx.length;
    parts.push(`${idx.filter(i => vals[i] < avg).length} จาก ${idx.length} ต่ำกว่าค่าเฉลี่ย ${f(avg)}`);
  }
  return parts.join(' · ');
}

function _glanceHeadline(chart) {
  if (!_glanceOpt(chart, 'headline')) return '';
  const id = chart.canvas.id || '';
  if (/^(zoom|drill|mgt)/i.test(id) || chart.canvas.closest('[data-force-light]')) return '';
  const plan = _glancePlan(chart, true);
  if (!plan || !plan.primary) return '';
  const pm = plan.primary, ds = chart.data.datasets[pm.index];
  const vals = (ds.data || []).map(_glanceFinite);
  const labels = vals.map((v, i) => _glanceLab(chart, i));
  const o = { fmt: v => _glanceFmt(pm.vScale, v), pct: _glanceFmt(pm.vScale, 1).endsWith('%') };
  if (!plan.timeLike) return glanceRangeSentence(labels, vals, o);
  const grain = chart.$gi && chart.$gi.grain;
  return glanceTrendSentence(labels, vals, Object.assign(o, {
    partial: _glancePartial(chart), note: chart.$gi && chart.$gi.note,
    unit: grain === 'month' ? 'เดือน' : grain === 'day' ? 'วัน' : 'ช่วง',
  }));
}

// Does the card already state numbers above this chart (a hand-written summary,
// KPI tiles)? Then a headline would only repeat it.
function _glanceHasSummary(canvas, card) {
  const scope = card || (canvas.parentElement && canvas.parentElement.parentElement);
  if (!scope) return false;
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  let txt = '';
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!(n.compareDocumentPosition(canvas) & Node.DOCUMENT_POSITION_FOLLOWING)) break;   // past the canvas
    const p = n.parentElement;
    if (p && p.closest('.glance-line, button, select, option, script, style')) continue;
    txt += n.nodeValue + ' ';
  }
  return /[▲▼]|\d[\d.,]*\s*(%|K\b|M\b)|฿\s?\d/.test(txt);
}

const _glanceIsRow = el => {
  if (!el) return false;
  const cs = getComputedStyle(el);
  if (cs.display.includes('flex')) return !cs.flexDirection.startsWith('column');
  if (cs.display.includes('grid')) return cs.gridTemplateColumns.trim().split(/\s+/).length > 1;
  return false;
};

function _glanceSetHeadline(chart, html) {
  const cv = chart.canvas, id = cv.id;
  let el = chart.$glanceLine && chart.$glanceLine.isConnected ? chart.$glanceLine
         : (id ? document.querySelector(`.glance-line[data-for="${CSS.escape(id)}"]`) : null);
  if (html && !el) {
    const card = cv.closest('.card');
    if (_glanceHasSummary(cv, card)) html = '';
    else {
      // Above the canvas wrapper, never as a new column of a flex/grid row.
      let anchor = cv.parentElement && cv.parentElement !== card ? cv.parentElement : cv;
      while (anchor.parentElement && anchor.parentElement !== card && _glanceIsRow(anchor.parentElement)) anchor = anchor.parentElement;
      if (!anchor.parentElement || _glanceIsRow(anchor.parentElement)) html = '';
      else {
        el = document.createElement('div');
        el.className = 'glance-line';
        if (id) el.dataset.for = id;
        el.style.cssText = 'font-size:12px;line-height:1.45;color:var(--text-secondary);margin:0 0 6px';
        anchor.parentElement.insertBefore(el, anchor);
      }
    }
  }
  chart.$glanceLine = el || null;
  if (!el) return;
  if (!html) { el.remove(); chart.$glanceLine = null; return; }
  if (el.innerHTML !== html) el.innerHTML = html;
}

const GLANCE_PLUGIN = {
  id: 'glance',

  // Config-time: give a lone solid, unfilled line a wash (Filler needs `fill`
  // set before the first layout), and room on the right for end labels.
  beforeInit(chart) {
    const cfg = chart.config, o = cfg.options || {};
    const go = o.plugins && o.plugins.glance;
    if (go === false || (cfg.type !== 'line' && cfg.type !== 'bar') || o.indexAxis === 'y') return;
    const sets = (cfg.data && cfg.data.datasets) || [];
    const yStacked = o.scales && o.scales.y && o.scales.y.stacked;
    if (cfg.type === 'line' && !(go && go.depth === false) && !yStacked) {
      const solid = sets.filter(ds => _glanceIsLine(chart, ds) && !ds.hidden && !(ds.borderDash || []).length
                                     && !_GLANCE_SKIP_RE.test(ds.label || ''));
      if (solid.length === 1 && solid[0].fill === undefined && typeof solid[0].borderColor === 'string') {
        const ds = solid[0];
        ds.fill = 'origin';
        if (ds.pointBackgroundColor === undefined) ds.pointBackgroundColor = ds.borderColor;
        if (ds.backgroundColor === undefined) ds.backgroundColor = ds.borderColor;
      }
    }
    if (go && go.labels === false) return;
    const ends = sets.filter(ds => _glanceIsLine(chart, ds) && !ds.hidden && !_GLANCE_SKIP_RE.test(ds.label || '')
                                   && !_glanceLabelled(chart, ds));
    if (!ends.length || ends.length > 4) return;
    const narrow = ((chart.canvas.parentElement && chart.canvas.parentElement.clientWidth) || 999) < 520;
    const n = (cfg.data.labels || []).length;
    const tag = _glanceInfo(chart).partial >= 0 ? ' MTD' : '';
    let need = 0;
    const ctx = chart.ctx;
    ctx.save();
    ctx.font = `600 ${narrow ? 10 : 11}px ${Chart.defaults.font.family}`;
    (narrow ? ends.slice(0, 1) : ends).forEach(ds => {
      const d = ds.data || [];
      let i = d.length - 1;
      while (i >= 0 && _glanceFinite(d[i]) == null) i--;
      if (i < 0 || (n && i < n - 1)) return;   // ends before the last x: label sits above, no room needed
      need = Math.max(need, ctx.measureText(_glanceCompact(_glanceFinite(d[i])) + '%' + tag).width + 14);
    });
    ctx.restore();
    if (!need) return;
    o.layout = o.layout || {};
    const p = o.layout.padding;
    if (typeof p === 'number') o.layout.padding = { top: p, bottom: p, left: p, right: Math.max(p, need) };
    else if (p && typeof p === 'object') p.right = Math.max(p.right || 0, need);
    else o.layout.padding = { right: need };
  },

  // Dates + the period in progress; a line's segment into that point is dashed.
  beforeUpdate(chart) {
    chart.$gi = _glanceInfo(chart);
    const p = chart.$gi.partial;
    ((chart.config.data && chart.config.data.datasets) || []).forEach(ds => {
      if (!_glanceIsLine(chart, ds)) return;
      if (!('$glanceSeg' in ds)) Object.defineProperty(ds, '$glanceSeg', { value: ds.segment, enumerable: false });
      const own = ds.$glanceSeg;
      if (p < 1 || !_glanceActual(ds) || _glanceFinite((ds.data || [])[p]) == null) { ds.segment = own; return; }
      ds.segment = Object.assign({}, own, {
        borderDash: c => c.p1DataIndex === p ? [4, 3]
                       : (own && typeof own.borderDash === 'function' ? own.borderDash(c) : own && own.borderDash),
      });
    });
  },

  afterUpdate(chart) {
    try { _glanceSetHeadline(chart, _glanceHeadline(chart)); } catch (e) { /* the chart matters more than its caption */ }
  },

  afterDestroy(chart) {
    if (chart.$glanceLine) chart.$glanceLine.remove();
    chart.$glanceLine = null;
  },

  // Depth: swap flat fills for gradients for this frame only (restored after
  // the datasets draw, so tooltips/legends/hover keep the real colours).
  beforeDatasetsDraw(chart) {
    const restore = [];
    chart.$glanceRestore = restore;
    chart.$glanceHollow = [];
    chart.$glanceWeekend = [];
    const gi = chart.$gi || {};
    // Weekend bands behind a daily axis.
    if (gi.grain === 'day' && gi.dates.length >= 7 && chart.options.indexAxis !== 'y' && _glanceOpt(chart, 'weekend')) {
      const xs = Object.values(chart.scales).find(s => s.axis === 'x');
      if (xs && xs.type === 'category') {
        const a = chart.chartArea, g = _glanceTheme(chart), ctx = chart.ctx;
        const step = Math.abs(xs.getPixelForValue(1) - xs.getPixelForValue(0));
        const wk = gi.dates.map(d => { const w = new Date(d.y, d.m - 1, d.d).getDay(); return w === 0 || w === 6; });
        ctx.save();
        for (let i = 0; i < wk.length; i++) {
          if (!wk[i]) continue;
          let j = i;
          while (j + 1 < wk.length && wk[j + 1]) j++;
          const x0 = Math.max(a.left, xs.getPixelForValue(i) - step / 2), x1 = Math.min(a.right, xs.getPixelForValue(j) + step / 2);
          ctx.fillStyle = g.key.includes('dark') ? 'rgba(148,163,184,0.09)' : 'rgba(100,116,139,0.07)';
          ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
          if (x1 - x0 >= 22) chart.$glanceWeekend.push((x0 + x1) / 2);
          i = j;
        }
        ctx.restore();
      }
    }
    const P = _glancePartial(chart);
    if (_glanceOpt(chart, 'depth') || P >= 0) {
      const horizontal = chart.options.indexAxis === 'y';
      const seen = new Map();
      const swap = (opts, prop, val) => {
        if (!seen.has(opts)) seen.set(opts, new Map());
        const m = seen.get(opts);
        if (!m.has(prop)) { m.set(prop, opts[prop]); restore.push([opts, prop, opts[prop]]); }
        opts[prop] = val;
      };
      const depth = _glanceOpt(chart, 'depth');
      const surf = _glanceRgba(_glanceTheme(chart).surface) || [255, 255, 255, 1];
      chart.getSortedVisibleDatasetMetas().forEach(m => {
        const stacked = m.vScale && m.vScale.options && m.vScale.options.stacked;
        if (m.type === 'bar') {
          m.data.forEach((el, i) => {
            if (!el || el.active || !el.options) return;
            const orig = seen.get(el.options)?.get('backgroundColor') ?? el.options.backgroundColor;
            const rgba = _glanceRgba(orig);
            if (!rgba || !(rgba[3] > 0)) return;
            if (i === P) {
              // The period in progress: a pale fill + dashed outline (drawn after the bars).
              // A per-bar copy of the options, so a shared object does not hollow every bar.
              const own = el.options;
              restore.push([el, 'options', own]);
              el.options = Object.assign(Object.create(Object.getPrototypeOf(own)), own, { backgroundColor: _glanceCss(rgba, rgba[3] * 0.18) });
              chart.$glanceHollow.push({ el, color: _glanceCss(rgba, Math.max(rgba[3], 0.7)) });
            } else if (depth && !stacked) {
              swap(el.options, 'backgroundColor', _glanceGrad(chart, rgba, surf, m.vScale, horizontal));
            }
          });
        } else if (depth && m.type === 'line' && m.dataset && m.dataset.options) {
          const lo = m.dataset.options;
          if (lo.fill !== true && lo.fill !== 'origin' && lo.fill !== 'start') return;
          const rgba = _glanceRgba(lo.borderColor);
          if (!rgba) return;
          const grad = chart.ctx.createLinearGradient(0, chart.chartArea.top, 0, chart.chartArea.bottom);
          grad.addColorStop(0, _glanceCss(rgba, 0.24));
          grad.addColorStop(1, _glanceCss(rgba, 0.01));
          swap(lo, 'backgroundColor', grad);
        }
      });
    }
    // Average hairline sits under the marks.
    const plan = _glanceOpt(chart, 'avg') ? _glancePlan(chart) : null;
    chart.$glanceAvg = null;
    // Only on a time axis: the mean of histogram buckets or day-of-week slots says nothing.
    if (plan && plan.primary && plan.timeLike && !(chart.options.plugins.annotation && Object.keys(chart.options.plugins.annotation.annotations || {}).length)
        && !chart.data.datasets.some(ds => _GLANCE_REF_RE.test(ds.label || ''))) {
      const P = _glancePartial(chart);
      const vals = (chart.data.datasets[plan.primary.index].data || []).map(_glanceFinite).filter((v, i) => v != null && i !== P);
      if (vals.length >= 4) {
        const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
        const sc = plan.primary.vScale, y = sc.getPixelForValue(avg);
        const a = chart.chartArea;
        if (y > a.top + 2 && y < a.bottom - 2) {
          const g = _glanceTheme(chart), ctx = chart.ctx;
          ctx.save();
          ctx.beginPath();
          ctx.setLineDash([4, 4]);
          ctx.lineWidth = 1;
          ctx.strokeStyle = g.theme.textDim;
          ctx.moveTo(a.left, Math.round(y) + 0.5);
          ctx.lineTo(a.right, Math.round(y) + 0.5);
          ctx.stroke();
          ctx.restore();
          chart.$glanceAvg = { y, text: 'เฉลี่ย ' + _glanceFmt(sc, avg) };
        }
      }
    }
  },

  afterDatasetsDraw(chart) {
    const hollow = chart.$glanceHollow || [];
    if (hollow.length) {
      const ctx = chart.ctx;
      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1.25;
      hollow.forEach(({ el, color }) => {
        const p = el.getProps(['x', 'y', 'base', 'width', 'height', 'horizontal'], true);
        ctx.strokeStyle = color;
        if (p.horizontal) ctx.strokeRect(Math.min(p.base, p.x) + 0.5, p.y - p.height / 2 + 0.5, Math.abs(p.x - p.base) - 1, p.height - 1);
        else ctx.strokeRect(p.x - p.width / 2 + 0.5, Math.min(p.y, p.base) + 0.5, p.width - 1, Math.abs(p.base - p.y) - 1);
      });
      ctx.restore();
    }
    chart.$glanceHollow = null;
    (chart.$glanceRestore || []).forEach(([o, p, v]) => { o[p] = v; });
    chart.$glanceRestore = null;
    const wkCaps = chart.$glanceWeekend || [];
    if (!_glanceOpt(chart, 'labels') && !chart.$glanceAvg && !wkCaps.length) return;
    const g = _glanceTheme(chart), ctx = chart.ctx, a = chart.chartArea;
    ctx.save();
    const placed = [];
    if (chart.$glanceAvg) {
      _glanceText(ctx, chart.$glanceAvg.text, a.left + 4, chart.$glanceAvg.y - 8, 'left', g.theme.text, g.surface, 10, 500);
    }
    const plan = _glanceOpt(chart, 'labels') ? _glancePlan(chart) : null;
    // A narrow chart (tablet, the full dashboard on a phone) keeps one series'
    // labels and smaller type, so the end labels do not pile up.
    const narrow = chart.width < 520, fs = narrow ? 10 : 11;
    {
      const free = (x, y, w) => !placed.some(p => Math.abs(p.y - y) < 13 && Math.abs(p.x - x) < (p.w + w) / 2 + 4)
                              && !(chart.$glanceAvg && Math.abs(chart.$glanceAvg.y - 8 - y) < 12 && x - w < a.left + 90);
      const put = (text, x, y, align, color, size, weight, halo = true) => {
        ctx.font = `${weight} ${size}px ${Chart.defaults.font.family}`;
        const w = ctx.measureText(text).width;
        // A centred label never spills over the y-axis ticks or off the canvas edge.
        if (align === 'center') x = Math.max(a.left + w / 2 + 2, Math.min(chart.width - w / 2 - 2, x));
        const cx = align === 'left' ? x + w / 2 : align === 'right' ? x - w / 2 : x;
        if (!free(cx, y, w)) return false;
        placed.push({ x: cx, y, w });
        _glanceText(ctx, text, x, y, align, color, halo ? g.surface : null, size, weight);
        return true;
      };
      const n = (chart.data.labels || []).length;
      // 1) latest value at the end of each line
      let ends = plan ? plan.lines : [];
      if (narrow && ends.length > 1) ends = ends.filter(m => m === plan.primary).concat(plan.primary ? [] : ends.slice(0, 1));
      ends.forEach(m => {
        const ds = chart.data.datasets[m.index];
        if (_glanceLabelled(chart, ds)) return;
        let i = m.data.length - 1;
        while (i >= 0 && (_glanceFinite(ds.data[i]) == null || !m.data[i] || m.data[i].skip)) i--;
        if (i < 1) return;
        const el = m.data[i], v = _glanceFinite(ds.data[i]);
        const color = typeof ds.borderColor === 'string' ? ds.borderColor : g.theme.text;
        const text = _glanceFmt(m.vScale, v) + (i === _glancePartial(chart) && _glanceActual(ds) ? _glanceTag(chart) : '');
        const atEnd = !n || i >= n - 1;
        const ok = atEnd ? put(text, el.x + 8, el.y, 'left', g.theme.labelStrong, fs, 600)
                         : put(text, el.x, el.y - 12 < 9 ? el.y + 13 : el.y - 12, 'center', g.theme.labelStrong, fs, 600);
        if (ok) _glanceDot(ctx, el.x, el.y, color, g.surface);
      });
      // 2) the primary series' peak (+ the latest bar on a time axis)
      const pm = plan && plan.primary;
      if (pm && !_glanceLabelled(chart, chart.data.datasets[pm.index])) {
        const ds = chart.data.datasets[pm.index];
        const vals = (ds.data || []).map(_glanceFinite);
        let hi = -1;
        const P = _glancePartial(chart);
        vals.forEach((v, i) => { if (v != null && i !== P && (hi < 0 || v > vals[hi])) hi = i; });
        const marks = [];
        if (pm.type === 'bar' && plan.timeLike) {
          let last = vals.length - 1;
          while (last >= 0 && vals[last] == null) last--;
          if (last >= 0) marks.push(last);
        }
        let lastLine = vals.length - 1;
        while (lastLine >= 0 && vals[lastLine] == null) lastLine--;
        if (hi >= 0 && vals[hi] > 0 && !(pm.type === 'line' && hi === lastLine) && !marks.includes(hi)) marks.push(hi);
        marks.forEach(i => {
          const el = pm.data[i];
          if (!el || el.skip) return;
          const v = vals[i], text = _glanceFmt(pm.vScale, v) + (i === P ? _glanceTag(chart) : '');
          const up = v >= 0;
          if (pm.type === 'bar') {
            // A bar that reaches the canvas top carries its label inside the tip.
            const inside = up && el.y - 8 < 9;
            const fill = _glanceRgba(Array.isArray(ds.backgroundColor) ? ds.backgroundColor[i] : ds.backgroundColor);
            const lum = fill ? (0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]) / 255 : 0;
            put(text, el.x, inside ? el.y + 11 : up ? el.y - 8 : el.y + 9, 'center',
                inside ? (fill && lum > 0.6 ? '#0f172a' : '#ffffff') : g.theme.labelStrong, fs, 600, !inside);
          } else if (put(text, el.x, el.y - 12 < 9 ? el.y + 13 : el.y - 12, 'center', g.theme.labelStrong, fs, 600)) {
            _glanceDot(ctx, el.x, el.y, typeof ds.borderColor === 'string' ? ds.borderColor : g.theme.text, g.surface);
          }
        });
      }
      // 3) name the weekend bands once, wherever the caption does not cover a label
      for (const cx of wkCaps) if (put('ส-อา', cx, a.top + 7, 'center', g.theme.textDim, 9, 500, false)) break;
    }
    ctx.restore();
  },

  // Doughnut centre: the total, or the top slice's share when the values are
  // already percentages (they sum to ~100, so a "total" would say nothing).
  afterDraw(chart) {
    if (chart.config.type !== 'doughnut' || !_glanceOpt(chart, 'centre')) return;
    if ((chart.config.plugins || []).length) return;   // a chart with its own inline plugin draws its own centre
    const meta = chart.getDatasetMeta(0), arc = meta && meta.data && meta.data[0];
    if (!arc || !arc.innerRadius || arc.innerRadius < 30) return;
    const ds = chart.data.datasets[0], vals = (ds.data || []).map(_glanceFinite);
    let sum = 0, top = -1;
    vals.forEach((v, i) => {
      if (v == null || v <= 0 || !chart.getDataVisibility(i)) return;
      sum += v;
      if (top < 0 || v > vals[top]) top = i;
    });
    if (top < 0 || sum <= 0) return;
    const pctData = Math.abs(sum - 100) < 1.5 && vals.every(v => v == null || v <= 100);
    const big = pctData ? `${parseFloat((vals[top] / sum * 100).toFixed(1))}%` : _glanceCompact(sum);
    let sub = pctData ? String((chart.data.labels || [])[top] ?? '') : 'รวม';
    if (sub.length > 14) sub = sub.slice(0, 13) + '…';
    const g = _glanceTheme(chart), ctx = chart.ctx;
    const r = arc.innerRadius, size = Math.max(12, Math.min(22, Math.round(r * 0.38)));
    ctx.save();
    ctx.font = `700 ${size}px ${Chart.defaults.font.family}`;
    if (ctx.measureText(big).width > r * 1.7) { ctx.restore(); return; }
    _glanceText(ctx, big, arc.x, arc.y - (sub ? size * 0.32 : 0), 'center', g.theme.labelStrong, g.surface, size, 700);
    if (sub) _glanceText(ctx, sub, arc.x, arc.y + size * 0.62, 'center', g.theme.text, g.surface, Math.max(10, Math.round(size * 0.5)), 500);
    ctx.restore();
  },
};

let activeTab       = 'home';
let activeSubtab    = 'mostOrdered';
let activeOpsSubtab = 'cohort';
let chartInstances = {};    // name → Chart instance
// allMonths is always an array — enforced by getter/setter
let _allMonths = [];
Object.defineProperty(window, 'allMonths', {
  get() { return _allMonths; },
  set(v) { _allMonths = Array.isArray(v) ? v : []; },
  configurable: true
});
let costModalSkuMap = {};
const _tabLastKey = {};   // tab name → last filter key used
function _filterKey() { return `${getBranch()}|${getMonthFrom()}|${getMonthTo()}`; }
let parsedCostRows  = [];

// cached data per tab
let cache = {};

// ── Security: HTML-escape any untrusted string before innerHTML interpolation.
// Branch names and SKU descriptions are user-entered / CSV-uploaded — never trust them.
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Safe interpolation into a single-quoted JS string inside an HTML attribute,
// e.g. onclick="f('${jsAttr(v)}')". JS-escape first, then HTML-escape.
const jsAttr = s => esc(String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'"));

// ── Up/down TEXT ink. #059669 / #16a34a / #dc2626 are fine for a border or a dot
// but fail as text (3.4-4.4:1 on the page, and they never adapt to dark mode).
// A helper whose colour feeds both a mark and a figure keeps the hex for the
// mark and passes the text use through here (tokens.css --good-ink / --bad-ink).
// #1f7a4d / #b0472d are the menu-impact pair in 07-home: 5.3:1 on white but
// only 3.1-3.3 on the dark card. #047857 / #b91c1c are the light inks themselves.
// #10b981 / #ef4444 are 2.5 / 3.8:1 on white: marks only, never text.
// #22c55e / #84cc16 (2.3 / 2.0:1) and the dark #065f46 / #991b1b / #7f1d1d (2.1-2.3:1
// on the dark card) are the same good/bad families.
const _GOOD_HEX = ['#059669', '#16a34a', '#1f7a4d', '#047857', '#10b981', '#22c55e', '#84cc16', '#065f46', '#15803d'],
      _BAD_HEX  = ['#dc2626', '#b0472d', '#b91c1c', '#ef4444', '#991b1b', '#7f1d1d'];
// Teal and rose (categories, the ordering/checklist accent): #0d9488 3.74 and #f43f5e 3.67:1 on white.
const _TEAL_HEX = ['#0d9488', '#14b8a6', '#0f766e'], _ROSE_HEX = ['#f43f5e', '#e11d48', '#be123c'];
// Cyan and sky (margin %, delivery, links): #0891b2 3.68, #06b6d4 2.43, #0ea5e9 2.77:1 on white.
const _CYAN_HEX = ['#06b6d4', '#0891b2', '#0e7490'], _SKY_HEX = ['#0ea5e9', '#0284c7', '#0369a1'];
// Navy / violet / fuchsia / pink (chart series, quadrant labels, price tiers): #4a70b0 and #7a66b8
// pass on white but drop to 2.9-3.9 on tints and the dark card; #d946ef 3.46, #ec4899 3.53:1 on white.
const _NAVY_HEX = ['#4a70b0', '#4f46e5', '#6366f1', '#2563eb', '#1d4ed8'], _VIOLET_HEX = ['#7a66b8', '#8b5cf6', '#7c3aed'];
const _FUCHSIA_HEX = ['#d946ef', '#c026d3', '#a21caf'], _PINK_HEX = ['#ec4899', '#db2777', '#be185d'];
// Amber / yellow and orange (warnings, "near target"): #f59e0b 2.15, #d97706 3.19, #eab308 1.9, #facc15 1.5,
// #f97316 2.8:1 on white; the deep #b45309 / #92400e / #9a3412 fail on the dark card.
const _AMBER_HEX  = ['#f59e0b', '#d97706', '#b45309', '#92400e', '#78350f', '#eab308', '#facc15', '#ca8a04', '#a16207', '#854d0e'],
      _ORANGE_HEX = ['#f97316', '#ea580c', '#c2410c', '#9a3412'];
// Neutral greys as text: #94a3b8 2.56 and #cbd5e1 1.5:1 on white; #64748b / #6b7280 fail on
// tints and the dark card. They become the muted text token, which adapts.
const _MUTED_HEX = ['#94a3b8', '#cbd5e1', '#64748b', '#6b7280', '#9ca3af'];
const _INK_OF = [[_GOOD_HEX, '--good-ink'], [_BAD_HEX, '--bad-ink'], [_TEAL_HEX, '--teal-ink'], [_ROSE_HEX, '--rose-ink'],
                 [_CYAN_HEX, '--cyan-ink'], [_SKY_HEX, '--sky-ink'], [_NAVY_HEX, '--navy-ink'], [_VIOLET_HEX, '--violet-ink'],
                 [_FUCHSIA_HEX, '--fuchsia-ink'], [_PINK_HEX, '--pink-ink'], [_AMBER_HEX, '--amber-ink'],
                 [_ORANGE_HEX, '--orange-ink'], [_MUTED_HEX, '--text-muted']];
const textInk = c => { const k = _INK_OF.find(([hexes]) => hexes.includes(c)); return k ? `var(${k[1]})` : c; };

// ── Formatters (guard against null / NaN / numeric-string from the API) ──────
const _num = n => { const v = Number(n); return (n == null || isNaN(v)) ? null : v; };
const fmt  = n => { const v=_num(n); return v==null ? '—' : v >= 1e6 ? (v/1e6).toFixed(2)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'K' : v.toFixed(0); };
const fmtN = n => { const v=_num(n); return v==null ? '—' : v.toLocaleString('th-TH',{maximumFractionDigits:0}); };
const fmtP = n => { const v=_num(n); return v==null ? '—' : v.toFixed(1)+'%'; };
const fmtM = n => { const v=_num(n); return v==null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(1)+'%'; };

// ── Fetch robustness (D3) + stale-response guard (D4) ───────────────────────
// loadGen increments on every tab switch / filter change. A loader captures the
// generation at start; if it has changed by the time a response arrives, the
// response is stale (the user moved on) and must NOT overwrite the new view.
let loadGen = 0;
let _chartsPending = false;   // set when mkChart was called before Chart.js finished loading
let _chartSetupDone = false;  // guards one-time plugin registration + global chart defaults
const STALE = Symbol('stale-response');

// Standard JSON fetch: checks response.ok and (optionally) drops stale responses.
// Pass the generation captured at loader start as `gen` to enable the stale guard.
// The one JSON call: fetch + r.json(), nothing else — deliberately identical to
// the `fetch(url).then(r => r.json())` it replaced at 200+ sites, so a non-OK
// JSON body still reaches the caller that checks `detail` / `available` today.
// The write token is attached by the global fetch wrapper above. This is the one
// place to add a timeout, a retry or an error toast for every call at once.
// The tab panels are not in the shell any more (tabs/NN-<name>.html, served as
// one bundle at /tabs.html). Load them into #tabsRoot before anything reads a
// panel id — init() awaits this first, so the DOM is complete at the same point
// of the boot it always was. The version comes from the shell, so a shell cached
// by the browser still fetches the bundle that matches it.
async function loadTabFragments() {
  const root = document.getElementById('tabsRoot');
  if (!root || root.childElementCount) return;
  const v = root.dataset.tabsV || '';
  const r = await fetch(`${API}/tabs.html${v ? '?v=' + v : ''}`);
  if (!r.ok) throw new Error('tabs.html ' + r.status);
  root.innerHTML = await r.text();
}

function api(url, opts) {
  return fetch(url, opts).then(r => r.json());
}

async function getJSON(path, { gen = null, signal } = {}) {
  const url = (path.startsWith('http') || path.startsWith('/')) ? path : `${API}${path}`;
  const res = await fetch(url, { signal });
  if (gen !== null && gen !== loadGen) throw STALE;   // user navigated away mid-flight
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${path}`);
  const data = await res.json();
  if (gen !== null && gen !== loadGen) throw STALE;
  return data;
}

// Surface otherwise-silent fetch/render failures instead of leaving a half-built tab.
function notifyError(msg) {
  let el = document.getElementById('globalErrToast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'globalErrToast';
    el.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:9999;max-width:360px;' +
      'background:#7f1d1d;color:#fff;padding:10px 14px;border-radius:var(--r-md);font-size:12px;' +
      'box-shadow:0 4px 16px rgba(0,0,0,.3);cursor:pointer;line-height:1.4';
    el.onclick = () => el.remove();
    document.body.appendChild(el);
  }
  el.textContent = '⚠ ' + msg + '  (คลิกเพื่อปิด)';
  clearTimeout(el._t);
  el._t = setTimeout(() => el.remove(), 8000);
}

// The quiet counterpart: a green toast for "saved / done" (two tabs already probe for it).
function notifySuccess(msg) {
  let el = document.getElementById('globalOkToast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'globalOkToast';
    el.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:9999;max-width:360px;' +
      'background:#065f46;color:#fff;padding:10px 14px;border-radius:var(--r-md);font-size:12px;' +
      'box-shadow:0 4px 16px rgba(0,0,0,.3);cursor:pointer;line-height:1.4';
    el.onclick = () => el.remove();
    document.body.appendChild(el);
  }
  el.textContent = '✓ ' + msg;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.remove(), 3500);
}

window.addEventListener('unhandledrejection', e => {
  if (e.reason === STALE) { e.preventDefault(); return; }            // expected — ignore
  const msg = (e.reason && e.reason.message) || String(e.reason || '');
  if (/abort/i.test(msg)) { e.preventDefault(); return; }            // expected on cancel
  console.error('Unhandled fetch/render error:', e.reason);
  notifyError('โหลดข้อมูลไม่สำเร็จ: ' + msg);
});

// chart registry alias used by zoom reset buttons
const charts = chartInstances;

function resetZoom(chartId) {
  const c = charts[chartId];
  if (c) c.resetZoom();
}

function mkChart(id, config) {
  // Vendor-load race: a tab loader can fire before chart.umd.min.js finishes
  // loading. Fail soft (the chart re-renders on the next tab/filter load) rather
  // than throwing a ReferenceError that aborts the whole loader.
  if (typeof Chart === 'undefined') { _chartsPending = true; return null; }
  ensureChartSetup();   // apply global defaults before the first chart is built
  if (chartInstances[id]) { chartInstances[id].destroy(); }
  const ctx = document.getElementById(id);
  if (!ctx) return null;
  // Charts are static by default — no mouse-wheel zoom (avoids accidental zoom while scrolling page).
  // Users who want zoom can click the dive-in button to open the full analysis modal.
  if (['line','bar','scatter'].includes(config.type)) {
    config.options = config.options || {};
    config.options.plugins = config.options.plugins || {};
    if (!config.options.plugins.zoom) {
      config.options.plugins.zoom = {
        zoom: { wheel: { enabled: false }, pinch: { enabled: false }, drag: { enabled: false }, mode: 'xy' },
        pan:  { enabled: false, mode: 'xy' },
      };
    }
  }
  // Apply theme-aware axis / grid / label colors (respects dark mode).
  // A canvas inside a [data-force-light] container (the printable Management
  // Report keeps a white-paper look in dark mode) always themes as light.
  applyChartTheme(config, ctx.closest('[data-force-light]') ? 'light' : undefined);
  chartInstances[id] = new Chart(ctx, config);
  // Auto-inject a "Dive in" zoom button on the chart canvas wrapper
  injectDiveBtn(id, ctx);
  return chartInstances[id];
}

// Centered moving-average smoother → a "trend curve" over a short series (the 8-day /
// 8-week revenue trend). The window clamps at the edges so the first/last points stay
// anchored to real data rather than drifting. Pure function of the input array.
function movingAvg(values, win = 3) {
  const n = values.length, half = Math.floor(win / 2);
  return values.map((_, i) => {
    let sum = 0, cnt = 0;
    for (let j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) {
      sum += values[j] || 0; cnt++;
    }
    return cnt ? sum / cnt : 0;
  });
}

function injectDiveBtn(chartId, canvas) {
  if (chartId.startsWith('zoom-') || chartId.startsWith('drill')) return; // skip modal charts
  const wrap = canvas.parentElement;
  if (!wrap || wrap.querySelector('.dive-btn[data-for="'+chartId+'"]')) return;
  wrap.style.position = wrap.style.position || 'relative';
  const btn = document.createElement('button');
  btn.className = 'dive-btn';
  btn.dataset.for = chartId;
  btn.title = 'Dive in (zoom + analysis)';
  btn.innerHTML = ICON.search;
  btn.onclick = () => diveInChart(chartId);
  wrap.appendChild(btn);
}

// ── Sortable tables ─────────────────────────────────────────────────────────
// makeSortable(id) turns the LAST header row of a table into click-to-sort
// columns (first click = high→low, click again = low→high, a third click
// restores the server's original order). Opt a column out with data-nosort.
//
// Cell values are read from data-sort when present, otherwise parsed out of the
// cell text — the tables here render "฿1,234", "12.3%", "+4.5", "−2 (Δ+1%)" and
// "-", so we take the FIRST numeric token and treat "-"/"" as missing (missing
// always sorts last, whichever direction, so empty rows never top the table).
function _tblSortVal(row, idx) {
  const cell = row.cells[idx];
  if (!cell) return { n: null, s: '' };
  const raw = cell.dataset.sort;
  const txt = (raw != null ? raw : cell.textContent).trim();
  if (txt === '' || txt === '-' || txt === '—') return { n: null, s: '' };
  // −  (U+2212, used for negatives in the reports) → ASCII minus
  const m = txt.replace(/−/g, '-').replace(/,/g, '').match(/-?\d+(\.\d+)?/);
  return { n: m ? parseFloat(m[0]) : null, s: txt.toLowerCase() };
}

function makeSortable(tableOrId) {
  const table = typeof tableOrId === 'string' ? document.getElementById(tableOrId) : tableOrId;
  if (!table || !table.tHead || !table.tBodies[0]) return;
  const headRow = table.tHead.rows[table.tHead.rows.length - 1];
  if (!headRow) return;
  const tbody = table.tBodies[0];
  // Remember the served order so a third click can put it back.
  Array.from(tbody.rows).forEach((r, i) => { if (!r.dataset.ord) r.dataset.ord = String(i); });
  Array.from(headRow.cells).forEach((th, idx) => {
    if (th.dataset.nosort != null || th.dataset.sortable) return;
    th.dataset.sortable = '1';
    th.classList.add('th-sortable');
    th.title = (th.title ? th.title + ' · ' : '') + 'คลิกเพื่อเรียง มาก→น้อย / น้อย→มาก';
    th.addEventListener('click', () => tblSort(th, idx));
  });
}

function tblSort(th, idx) {
  const table = th.closest('table');
  if (!table || !table.tBodies[0]) return;
  const headRow = th.parentElement;
  const dir = th.dataset.sortDir === 'desc' ? 'asc' : th.dataset.sortDir === 'asc' ? '' : 'desc';
  Array.from(headRow.cells).forEach(c => {
    delete c.dataset.sortDir;
    c.classList.remove('sorted-desc', 'sorted-asc');
  });
  if (dir) { th.dataset.sortDir = dir; th.classList.add('sorted-' + dir); }
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  rows.sort((a, b) => {
    if (!dir) return (+a.dataset.ord || 0) - (+b.dataset.ord || 0);
    const va = _tblSortVal(a, idx), vb = _tblSortVal(b, idx);
    if (va.n == null && vb.n == null) return va.s.localeCompare(vb.s, 'th') * (dir === 'desc' ? -1 : 1);
    if (va.n == null) return 1;            // missing values sink, both directions
    if (vb.n == null) return -1;
    return dir === 'desc' ? vb.n - va.n : va.n - vb.n;
  });
  rows.forEach(r => tbody.appendChild(r));
  // Rank columns must count the CURRENT order, not the one they were built in.
  rows.forEach((r, i) => {
    const c = r.querySelector('.rank-cell');
    if (c) c.textContent = String(i + 1);
  });
}

function badge(pct) {
  if (pct == null) return '<span class="pill pill-neu">N/A</span>';
  const cls = pct > 0 ? 'pill-up' : pct < 0 ? 'pill-down' : 'pill-neu';
  const arrow = pct > 0 ? '▲' : pct < 0 ? '▼' : '—';
  return `<span class="pill ${cls}">${arrow} ${Math.abs(pct).toFixed(1)}%</span>`;
}

function trendBadge(pct) {
  if (pct == null) return '';
  const cls = pct > 0 ? 'pill-up' : pct < 0 ? 'pill-down' : 'pill-neu';
  const arrow = pct > 0 ? '▲' : pct < 0 ? '▼' : '—';
  return `<span class="pill ${cls}">${arrow} ${Math.abs(pct).toFixed(1)}%</span>`;
}

function mkSparkline(values, color, w=72, h=26) {
  if (!values || values.length < 2) return '';
  const mn = Math.min(...values), mx = Math.max(...values);
  const range = mx - mn || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - mn) / range) * (h - 6) - 3;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" fill="none" style="opacity:0.75;flex-shrink:0"><polyline points="${pts}" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

// ── Filter helpers ─────────────────────────────────────────────
const getBranch    = () => document.getElementById('selBranch').value;
const getGroup     = () => document.getElementById('selGroup')?.value || '';
const getMonthFrom = () => document.getElementById('selMonthFrom').value;
const getMonthTo   = () => document.getElementById('selMonthTo').value;
const getMonth     = () => getMonthTo() || getMonthFrom(); // backward compat for single-month contexts
const getVariant   = () => document.getElementById('selVariant')?.value || '';

// A per-dish margin / cost% is only meaningful for items that carry a real
// à-la-carte price. Buffet (B) dishes are included in the buffet package
// (revenue ≈ 0 per dish — the price lives on the buffet-package SKU), so their
// margin explodes to garbage (e.g. -29,709,896%). Exclude them from margin
// analysis; their ingredient cost is still counted in chain COGS elsewhere.
function _hasMenuPrice(r){
  if (!r) return false;
  const d = r.description || '';
  if (/^\(\s*B\s*\)/.test(d)) return false;                       // (B) = buffet-included dish
  if (/BUFFET/i.test((r.sku_code||'') + ' ' + (r.category||''))) return false;
  return (r.revenue_ex_vat ?? r.revenue ?? 0) > 0;
}
function qp(extra = {}) {
  const p = {};
  if (getBranch())    p.branch     = getBranch();
  if (getGroup())     p.group      = getGroup();
  if (getMonthFrom()) p.month_from = getMonthFrom();
  if (getMonthTo())   p.month_to   = getMonthTo();
  // Day-precise mode (daily/weekly granularity, incl. the custom day range): also
  // pass exact date_from/date_to. Day-capable endpoints prefer these; month-grain
  // endpoints ignore them and use the (enclosing) month range above.
  const g = (typeof currentGranularity !== 'undefined') ? currentGranularity : 'monthly';
  if (g === 'daily' || g === 'weekly') {
    const df = document.getElementById('selDateFrom')?.value;
    const dt = document.getElementById('selDateTo')?.value;
    if (df) p.date_from = df;
    if (dt) p.date_to   = dt;
  }
  if (getVariant())   p.variant    = getVariant();
  Object.assign(p, extra);
  const s = new URLSearchParams(p).toString();
  return s ? '?' + s : '';
}

// Params for the KPI summary cards. Same as qp(), but in daily/weekly mode the
// summary follows the date-range pickers instead of the month range (the month
// selects are hidden then, so their values are stale). /api/summary computes the
// window from sales_transaction when date_from/date_to are present.
function summaryParams() {
  const g = (typeof currentGranularity !== 'undefined') ? currentGranularity : 'monthly';
  if (g === 'daily' || g === 'weekly') {
    const p = {};
    if (getBranch()) p.branch = getBranch();
    if (getGroup())  p.group  = getGroup();
    const df = document.getElementById('selDateFrom')?.value;
    const dt = document.getElementById('selDateTo')?.value;
    if (df) p.date_from = df;
    if (dt) p.date_to   = dt;
    const s = new URLSearchParams(p).toString();
    return s ? '?' + s : '';
  }
  return qp();
}

// ── Calendar year quick selector (Phase 7) ─────────────────────────
// Populate selYearQuick from window.allMonths (set by loadMonths in dashboard init).
function _populateYearQuick() {
  const sel = document.getElementById('selYearQuick');
  if (!sel) return;
  const months = Array.isArray(window.allMonths) ? window.allMonths.slice() : [];
  const years = [...new Set(months.map(m => m.slice(0, 4)))].sort();
  // Preserve placeholder option, rebuild rest
  while (sel.options.length > 1) sel.remove(1);
  years.forEach(y => {
    const o = document.createElement('option');
    o.value = y; o.textContent = y;
    sel.appendChild(o);
  });
}
// Set month range to Jan–Dec of a given calendar year (clamped to data availability).
// fullYear=true → always Jan-Dec; false → Jan-current month for YTD behaviour.
function setYearRange(year, fullYear = true) {
  const months = Array.isArray(window.allMonths) ? window.allMonths : [];
  if (!months.length) return;
  const yStr = String(year);
  const inYear = months.filter(m => m.startsWith(yStr));
  if (!inYear.length) {
    alert(`ไม่มีข้อมูลในปี ${yStr}`);
    return;
  }
  const from = fullYear ? `${yStr}-01` : inYear[0];
  const to = fullYear ? `${yStr}-12` : inYear[inYear.length - 1];
  // Snap to available months (some months may be missing in data)
  const fromSnap = months.find(m => m >= from) || inYear[0];
  const toSnap   = [...months].reverse().find(m => m <= to) || inYear[inYear.length - 1];
  const selFrom = document.getElementById('selMonthFrom');
  const selTo   = document.getElementById('selMonthTo');
  if (selFrom) selFrom.value = fromSnap;
  if (selTo)   selTo.value   = toSnap;
  // Sync the year dropdown display
  const ySel = document.getElementById('selYearQuick');
  if (ySel) ySel.value = yStr;
  if (typeof onFilterChange === 'function') onFilterChange();
}
function onYearQuickChange(val) {
  if (!val) return;  // placeholder selected — no-op
  setYearRange(parseInt(val, 10), true);
}
// YoY: set range to current-year YTD; user can switch to previous via dropdown for compare.
function setYoYRange() {
  const months = Array.isArray(window.allMonths) ? window.allMonths : [];
  if (!months.length) return;
  const latest = months[months.length - 1];
  const curYear = parseInt(latest.slice(0, 4), 10);
  const prevYear = curYear - 1;
  // Set to span Jan(prevYear) → latest(curYear) so charts show YoY context
  const selFrom = document.getElementById('selMonthFrom');
  const selTo   = document.getElementById('selMonthTo');
  if (selFrom) selFrom.value = months.find(m => m >= `${prevYear}-01`) || months[0];
  if (selTo)   selTo.value   = latest;
  const ySel = document.getElementById('selYearQuick');
  if (ySel) ySel.value = '';
  if (typeof onFilterChange === 'function') onFilterChange();
}

// Phase 5 — click any branch name/box to drill into it globally.
// Calling twice on the same branch toggles back to "all branches".
function drillIntoBranch(branchName) {
  const sel = document.getElementById('selBranch');
  if (!sel) return;
  sel.value = (sel.value === branchName) ? '' : branchName;
  sel.style.transition = 'box-shadow 0.5s';
  sel.style.boxShadow = sel.value ? '0 0 0 3px rgba(31,58,107,0.45)' : '';
  setTimeout(() => { sel.style.boxShadow = ''; }, 700);
  if (typeof onFilterChange === 'function') onFilterChange();
}

// Phase 4 — global period presets. Sets selMonthFrom/selMonthTo then reloads.
// Highlights the active preset button; clears it when the user changes selectors manually.
function setPeriodPreset(preset) {
  const months = Array.isArray(window.allMonths) ? window.allMonths : [];
  if (!months.length) return;
  const latest = months[months.length - 1];
  const latestDate = new Date(latest + '-01');
  let from = '';
  if (preset === 'all') {
    from = '';
  } else if (preset === '3m') {
    const d = new Date(latestDate); d.setMonth(d.getMonth() - 2);
    from = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
  } else if (preset === '6m') {
    const d = new Date(latestDate); d.setMonth(d.getMonth() - 5);
    from = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
  } else if (preset === '12m') {
    const d = new Date(latestDate); d.setMonth(d.getMonth() - 11);
    from = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
  } else if (preset === 'ytd') {
    from = latest.slice(0, 4) + '-01';
  }
  // Snap to nearest available month
  const snapFrom = from ? (months.find(m => m >= from) || months[0]) : '';
  const selFrom = document.getElementById('selMonthFrom');
  const selTo   = document.getElementById('selMonthTo');
  if (selFrom) selFrom.value = snapFrom;
  if (selTo)   selTo.value   = '';
  // Highlight active preset button
  document.querySelectorAll('.period-preset').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('preset-' + preset);
  if (btn) btn.classList.add('active');
  if (typeof onFilterChange === 'function') onFilterChange();
}
// Clear preset highlight when user manually changes period selectors
(function() {
  function clearPresetHighlight() {
    document.querySelectorAll('.period-preset').forEach(b => b.classList.remove('active'));
  }
  document.addEventListener('DOMContentLoaded', () => {
    ['selMonthFrom','selMonthTo'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', clearPresetHighlight);
    });
  });
})();

async function onGroupChange() {
  // Filter the branch dropdown to show only branches in the selected group
  const grp = getGroup();
  const url = grp ? `${API}/api/branches?group=${grp}` : `${API}/api/branches`;
  const data = await api(url);
  const sel = document.getElementById('selBranch');
  const prev = sel.value;
  sel.innerHTML = '<option value="">ทุกสาขา</option>' +
    data.map(b => `<option value="${esc(b.name)}">${esc(b.name)}</option>`).join('');
  // keep existing branch if still valid; else reset
  sel.value = data.some(b => b.name === prev) ? prev : '';
  onFilterChange();
}

// ── Tab switching ──────────────────────────────────────────────
const PAGE_TITLES = { home:'Daily Brief', overall:'Overall', action:'ต้องดูวันนี้ (Action Center)', buffetperf:'ผลงานบุฟเฟ่ต์ (Buffet Performance)', menu:'Menu Analysis', trend:'Trend', cost:'Cost Analysis', profit:'วิเคราะห์กำไร', pnl:'P&L เชิงลึก (P&L Deep Dive)', compare:'เปรียบเทียบช่วงเวลา (A vs B)', report:'Monthly Report (ฉบับเต็ม)', mgtreport:'Management Report (Daily · Weekly · Monthly)', ops:'วิเคราะห์เชิงลึก', settings:'Settings', guest:'Guest & SPH', benchmark:'Branch Benchmarks', engineering:'วิเคราะห์เมนู', staff:'Staff Scorecard', discounts:'Discounts & Leakage', childbuffet:'บุฟเฟ่ต์เด็ก', timeflow:'Time & Flow', gpbranch:'GP รายสาขา (Branch GP — NEW BoM)', branchbom:'BoM วิเคราะห์รายสาขา', bomcost:'BoM Cost Analysis', simhub:'Simulators (รวมตัวจำลอง)', menucompare:'เทียบเมนู A/B', menuefficiency:'เป้าลดต้นทุน (Menu Cost Efficiency)', menuactionboard:'ปรับเมนู (Menu Action Board)', menuopportunities:'โอกาสเมนู (Menu Opportunities)', feedback:'เสียงลูกค้า (Customer Feedback)', checklist:'Checklist เสียงลูกค้า (Go-live · การตลาด)', coverage:'ความครอบคลุมต้นทุน (Cost Coverage)', pnlbranch:'P&L รายสาขา (Branch P&L · BoM-based)', more:'Insight เพิ่มเติม (More Analytics)', manual:'คู่มือการใช้งาน', dailysales:'ยอดขายรายวัน (Daily Sales)', costhead:'ต้นทุน/หัว รายสาขา (BoM)', costheadtop60:'Top 60 ต้นทุน/หัว · สาขา Master (เทียบภาพรวม)', menuimpact:'วิเคราะห์วัตถุดิบ (Menu Impact — มี vs ไม่มี)', pnlbill:'P&L ต่อบิล (Per-bill · BoM)', opsreport:'รายงานปฏิบัติการ (Payment · Void · โต๊ะ · โปรโมชัน)', cashctl:'เงินสดขาด/เกิน (Cash Over/Short)', custtype:'ประเภทลูกค้า & Delivery', modifiers:'ความต้องการพิเศษ (Modifiers)', pricezone:'ราคา & โซนโต๊ะ', cancelbill:'บิลยกเลิก/ลบ (Voided Bills)', closetime:'เวลาปิดร้าน / ความสดของข้อมูล (Data Freshness)', heavyeater:'ต่อหัว & นักกินหนัก (Per-cover & Heavy Eaters)', ordering:'แผนสั่งวัตถุดิบรายสาขา (Ingredient Order Plan)', ordersetup:'ตั้งค่าการสั่งซื้อ (ขนาดบรรจุ · ปฏิทินเทศกาล)', ingpurchase:'ยอดซื้อวัตถุดิบ เทียบ ยอดขาย รายสาขา (รายสัปดาห์)', bomcenter:'BoM Center — ทะเบียนวัตถุดิบ · ราคารายเดือน · แปลงหน่วย', branchstock:'สาขาของฉัน · ตรวจนับ · สั่งสินค้า · รับสินค้า · ผลต่างการใช้', orderhub:'ศูนย์สั่งสินค้า · คิวใบสั่งซื้อ · ตรวจการสั่ง · สรุปผลต่างรายเดือน · ปิดงวด' };

// Every sidebar tab, once. `load` is what runs when the tab is opened (an arrow,
// because the loaders are defined in later slices and must resolve at call time) (or the
// global filter changes); a tab without one is static. switchTab() walks these
// keys to show/hide panels and loadActiveTab() dispatches through them — the
// 47-branch if-chain and the 48-name literal list this replaces had to be kept
// in step by hand, in two different files. TAB_MODULE (permissions) keys the
// same names.
const TABS = {
  overall: { load: () => loadOverall() },
  action: { load: () => loadActionCenter() },
  menu: { load: () => { loadMenuSubtab(); renderMenuSalesNarrative(); } },
  trend: { load: () => loadTrend() },
  cost: { load: () => loadCostAnalysis() },
  profit: { load: () => loadProfitAnalysis() },
  pnl: { load: () => loadPnlTab() },
  compare: { load: () => initCompare() },
  report: { load: () => loadReport() },
  mgtreport: { load: () => loadMgtReport() },
  ops: { load: () => loadOpsSubtab() },
  settings: { load: () => loadSettingsSubtab() },
  home: { load: () => loadHome() },
  dailysales: { load: () => loadDailySales(), follow: { date: 'dsDate' } },
  costhead: { load: () => loadCostHead(), follow: { date: 'chDate' } },
  menuimpact: { load: () => loadMenuImpact() },
  pnlbill: { load: () => loadPnlBill(), follow: { date: 'pbDate' } },
  opsreport: { load: () => loadOpsReport(), follow: { month: 'opMonth', prepare: () => _opFillMonths() } },
  ordering: { load: () => loadOrderPlan() },
  ingpurchase: { load: () => loadIngPurchase() },
  ordersetup: { load: () => loadOrderSetup() },
  bomcenter: { load: () => loadBomCenter() },
  branchstock: { load: () => loadBranchStock() },
  orderhub: { load: () => loadOrderHub() },
  cashctl: { load: () => loadCashCtl(), follow: { month: 'ccMonth' } },
  custtype: { load: () => loadCustType(), follow: { month: 'ctMonth' } },
  modifiers: { load: () => loadModifiers() },
  pricezone: { load: () => loadPriceZone(), follow: { month: 'pzMonth' } },
  cancelbill: { load: () => loadCancelBill(), follow: { month: 'cbMonth' } },
  closetime: { load: () => loadCloseTime(), follow: { date: 'ctmDate' } },
  guest: { load: () => loadGuest() },
  benchmark: { load: () => loadBenchmark() },
  engineering: { load: () => loadEngineering() },
  buffetperf: { load: () => loadBuffetPerformance() },
  staff: { load: () => loadStaff() },
  discounts: { load: () => loadDiscountsTab() },
  childbuffet: { load: () => loadChildBuffet() },
  timeflow: { load: () => loadTimeflow() },
  gpbranch: { load: () => loadGpBranch() },
  branchbom: { load: () => loadBranchBom(), follow: { monthRange: ['bbomMonthFrom', 'bbomMonthTo'], prepare: () => _bbomInitFilters() } },
  bomcost: { load: () => loadBomCostTab() },
  simhub: { load: () => loadSimhub() },
  menucompare: { load: () => loadMenuCompare() },
  menuefficiency: { load: () => loadMCE() },
  menuactionboard: { load: () => loadMenuActionBoard() },
  menuopportunities: { load: () => loadMenuOpportunities() },
  feedback: { load: () => loadFeedback() },
  checklist: { load: () => loadChecklist() },
  coverage: { load: () => loadCostCoverage() },
  pnlbranch: { load: () => loadPnlBranch(), follow: { month: 'pnlMonthSel', prepare: () => _pnlFillMonths() } },
  more: { load: () => loadMoreAnalytics() },
  heavyeater: { load: () => loadHeavyEaters(), follow: { dateRange: ['heFrom', 'heTo'] } },
  manual: {},
  costheadtop60: { load: () => loadCostHeadTop60() },
};

function loadActiveTab() {
  loadGen++;   // invalidate any in-flight responses from a previous tab/filter
  cache = {};  // clear tab cache on filter change
  const t = TABS[activeTab];
  if (!t || !t.load) return;
  if (!t.follow) return t.load();
  const gen = loadGen;
  return _followGlobal(activeTab, t.follow).then(() => { if (gen === loadGen) return t.load(); });
}

// A tab that keeps its OWN picker declares in TABS how it follows the global
// period, and the registry snaps that picker before the loader runs. Until
// 2026-09 every such loader had to remember to call _syncExtra* itself, and a
// tab that forgot silently ignored the top bar (js/07-home.js keeps the
// snapping helpers). A hand pick on the local picker still wins until the
// global period changes again — the helpers remember the last global value.
//   month:      select id — one month, snapped to the global END month
//   date:       input id  — one day, snapped to the global period's end day
//   dateRange:  [from, to] input ids — the global day range
//   monthRange: [from, to] select ids — the global month range
//   prepare:    async fn that fills the picker's options first; a month select
//               without one is filled by _extraMonths (the /api/months list)
async function _followGlobal(tabKey, f) {
  if (f.prepare) await f.prepare();
  else if (f.month) await _extraMonths(f.month);
  if (f.month) _syncExtraMonth(f.month, tabKey);
  if (f.date) _syncExtraDate(f.date, tabKey);
  if (f.dateRange) _syncExtraDateRange(f.dateRange[0], f.dateRange[1], tabKey);
  if (f.monthRange) _syncExtraMonthRange(f.monthRange[0], f.monthRange[1], tabKey);
}

function switchTab(name, direct) {
  // เมนูทำเงิน = the menu tab's Menu Engineering view (owner, 2026-10-01). The
  // standalone page (it adds a category filter) stays one click away from that
  // view: switchTab('engineering', true).
  if (name === 'engineering' && !direct && tabAllowed('menu')) {
    activeSubtab = 'engineering';
    switchTab('menu');
    switchSubtab('engineering');
    return;
  }
  // Permission guard — refuse tabs the current role can't access (server enforces too).
  if (!tabAllowed(name)) { name = firstAllowedTab(); }
  activeTab = name;
  // Reading mode hides the app chrome via a <body> class for the Management
  // Report; without this it would follow the reader into every other tab.
  if (name !== 'mgtreport') { try { _mgtReadingOff(); } catch (e) {} }
  try { navigator.sendBeacon(API + '/api/usage/tab', JSON.stringify({tab: name})); } catch(e) {}
  Object.keys(TABS).forEach(t => {
    document.getElementById('nav-' + t)?.classList.toggle('active', t === name);
    const el = document.getElementById('tab-' + t);
    if (!el) return;
    if (t === name) {
      el.style.display = '';
      el.classList.remove('hidden', 'tab-active');
      void el.offsetWidth;
      el.classList.add('tab-active');
    } else {
      el.style.display = 'none';
      el.classList.add('hidden');
      el.classList.remove('tab-active');
    }
  });
  // Hide top-bar filters in Settings + Compare (Compare has its own pickers)
  const hideFilters = (name === 'settings' || name === 'compare');
  document.getElementById('selBranch').closest('div').classList.toggle('invisible', hideFilters);
  // Daily Brief is fixed to "yesterday + month-to-date" — it ignores the period
  // range entirely (only the branch filter applies). Hide the period controls and
  // the Quick-load bar here so they don't look broken when they have no effect.
  const hidePeriod = (name === 'home' || name === 'mgtreport');
  document.querySelector('[aria-label^="ความถี่ข้อมูล"]')?.classList.toggle('invisible', hidePeriod);
  document.getElementById('dateRangePickers')?.classList.toggle('invisible', hidePeriod);
  document.getElementById('monthRangePickers')?.classList.toggle('invisible', hidePeriod);
  const _pb = document.getElementById('presetBar');
  if (_pb) _pb.style.display = hidePeriod ? 'none' : '';
  // สาขาของฉัน has its own branch (the login's) and its own dates — no chain filters at all,
  // removed from the layout (not just invisible) so a phone shows the count list first
  const ownPage = (name === 'branchstock' || name === 'orderhub');
  document.getElementById('selBranch').closest('div').classList.toggle('hidden', ownPage);
  document.querySelector('[aria-label^="ความถี่ข้อมูล"]')?.classList.toggle('hidden', ownPage);
  document.getElementById('dateRangePickers')?.classList.toggle('hidden', ownPage);
  document.getElementById('monthRangePickers')?.classList.toggle('hidden', ownPage);
  if (_pb && ownPage) _pb.style.display = 'none';
  document.getElementById('pageTitle').textContent = _noEmoji(PAGE_TITLES[name] || name);
  _updateClusterBar(name);
  // Reset buffet subtab to overview whenever user navigates to/from buffetperf
  if (name === 'buffetperf') switchBperfTab('overview');
  showLoadingBar();
  // Free charts that belong to the tab we just left. Chart.js instances are not
  // garbage-collected while registered, so without this they pile up (~80 live
  // after a full session), each holding a resize listener that fires on every
  // switch/resize and keeps the renderer busy. Each tab rebuilds its own charts
  // via mkChart() when re-opened (loadActiveTab below), so dropping them is safe.
  for (const [id, inst] of Object.entries(chartInstances)) {
    const panel = inst && inst.canvas && inst.canvas.closest('[id^="tab-"]');
    if (panel && panel.id !== 'tab-' + name) {
      try { inst.destroy(); } catch(e){}
      delete chartInstances[id];
    }
  }
  // Re-size remaining (now just this tab's) charts so any rendered while hidden
  // get correct dimensions.
  setTimeout(() => { Object.values(chartInstances).forEach(c => { try { c.resize(); } catch(e){} }); }, 30);
  Promise.resolve(loadActiveTab()).finally(() => hideLoadingBar());
}

// Lean-overhaul Phase 2: every analysis area is ONE sidebar entry + a cluster
// bar of related views on the page itself. tabs[0] of each cluster is its
// sidebar entry (applyPermissions' safety net relies on that ordering).
// Regrouped 2026-10-01 (owner): one entry per question - ยอดขาย / ลูกค้า &
// บุฟเฟ่ต์ / เมนู / ต้นทุน / กำไร / ส่วนลด & เงิน / ปฏิบัติการ / จัดซื้อ - and
// each group sits inside one permission area as far as it can, so a role sees
// whole groups instead of half-locked ones. A third element lists alias tabs
// that light the same chip (a sub-view of that page, not a chip of its own).
const TAB_CLUSTERS = [
  { key: 'sales',  label: 'ยอดขาย',          tabs: [['overall','ภาพรวม & KPI'],['dailysales','ยอดขายรายวัน'],['trend','แนวโน้ม'],['timeflow','ช่วงเวลา'],['custtype','ช่องทาง & ประเภทลูกค้า'],['compare','เทียบ A/B'],['more','Insight เพิ่มเติม']] },
  { key: 'buffet', label: 'ลูกค้า & บุฟเฟ่ต์', tabs: [['buffetperf','ผลงานบุฟเฟ่ต์'],['guest','ลูกค้า & ยอด/หัว'],['childbuffet','บุฟเฟ่ต์เด็ก']] },
  { key: 'menu',   label: 'เมนู',            tabs: [['menu','วิเคราะห์เมนู',['engineering']],['menuactionboard','ปรับเมนู'],['menuopportunities','โอกาสเมนู'],['menucompare','เทียบเมนู A/B']] },
  { key: 'cost',   label: 'ต้นทุน',           tabs: [['cost','ภาพรวม'],['bomcost','BoM Cost'],['costhead','ต้นทุน/หัว',['costheadtop60']],['heavyeater','นักกินหนัก'],['menuimpact','วิเคราะห์วัตถุดิบ'],['menuefficiency','เป้าลดต้นทุน'],['coverage','ความครอบคลุม'],['branchbom','BoM รายสาขา'],['simhub','Simulators']] },
  { key: 'profit', label: 'กำไร',            tabs: [['gpbranch','GP รายสาขา'],['pnlbranch','P&L รายสาขา'],['pnlbill','P&L ต่อบิล'],['profit','กำไร SKU'],['benchmark','Benchmark'],['pnl','P&L เชิงลึก']] },
  { key: 'money',  label: 'ส่วนลด & เงิน',    tabs: [['discounts','ส่วนลด'],['staff','พนักงาน'],['cashctl','เงินสดขาด/เกิน'],['cancelbill','บิลยกเลิก']] },
  { key: 'ops',    label: 'ปฏิบัติการ',       tabs: [['opsreport','รายงานปฏิบัติการ'],['ops','เชิงลึก'],['modifiers','ความต้องการพิเศษ'],['pricezone','ราคา/โซน'],['closetime','ปิดร้าน/ข้อมูล']] },
  { key: 'supply', label: 'BoM Center & จัดซื้อ', tabs: [['bomcenter','วัตถุดิบ & ราคา'],['ordering','แผนสั่งวัตถุดิบ'],['ingpurchase','ยอดซื้อ vs ยอดขาย'],['ordersetup','ตั้งค่าการสั่งซื้อ']] },
];
const _inChip = (t, name) => t[0] === name || (t[2] || []).includes(name);
const clusterOf = name => TAB_CLUSTERS.find(c => c.tabs.some(t => _inChip(t, name)));
// Group colours (docs/ui-kit/theme-groups.css): the group a tab belongs to -
// its cluster's key, else one of the stand-alone entries below.
const _GROUP_OF_LOOSE = { feedback: 'feedback', checklist: 'feedback' };
function _groupOf(name) { const cl = clusterOf(name); return cl ? cl.key : (_GROUP_OF_LOOSE[name] || 'home'); }
// Tag every sidebar item with its group once (the scripts load after the markup).
try {
  document.querySelectorAll('#sidebar .nav-item[id^="nav-"]').forEach(el => { el.dataset.g = _groupOf(el.id.slice(4)); });
} catch (e) { /* no sidebar on this page */ }
// ── Kill list — ON HOLD (owner's call, 2026-09-05) ─────────────────────────
// 47 days of usage telemetry (2026-07-20 → 09-05, Settings → การใช้งานจริง)
// found 17 tabs nobody opened (docs/LEAN_KILL_LIST_2026-09.html). The app is
// not yet used by everyone, so NOTHING is hidden or deleted: every tab shows in
// its cluster bar as before and the counters keep running. KILL_CANDIDATES is
// the list under watch (the Settings panel shows it with live counts);
// SOFT_KILL — what the cluster bar folds behind a "⋯" chip — stays EMPTY. To
// resume stage 1 later, move names from KILL_CANDIDATES into SOFT_KILL and set
// KILL_REVIEW_DATE. `action` has had no menu entry since before; `pnl` was brought back
// into the กำไร bar on 2026-10-01 (owner).
const KILL_CANDIDATES = ['action', 'pnl', 'profit', 'compare', 'costhead', 'pnlbill', 'modifiers', 'pricezone',
  'cancelbill', 'closetime', 'benchmark', 'staff', 'simhub', 'menuefficiency', 'menuopportunities', 'coverage',
  'costheadtop60'];
const SOFT_KILL = new Set([]);          // on hold — nothing folded
const KILL_REVIEW_DATE = '';            // no deletion date while on hold
// Formal theme: no emoji in the chrome (page title, cluster tabs). The labels in
// PAGE_TITLES / TAB_CLUSTERS still carry them; strip at render so the data stays put.
const _EMOJI_RE = /(?:[\u{1F000}-\u{1FAFF}\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\u2190-\u21FF\u2139\u3030\u303D][\uFE0F\u200D]*)+/gu;
function _noEmoji(s) { return String(s).replace(_EMOJI_RE, '').replace(/\s{2,}/g, ' ').trim(); }

let _clusterMoreOpen = false;

function _updateClusterBar(name) {
  const bar = document.getElementById('clusterBar');
  if (!bar) return;
  const cl = clusterOf(name);
  document.body.dataset.group = _groupOf(name);   // theme-groups.css colours the page by group
  // The group you are in stays lit in the sidebar, whichever of its views is open
  // (its entry is tabs[0], or the first member the role can open - applyPermissions).
  if (cl) {
    const entry = cl.tabs.map(([t]) => document.getElementById('nav-' + t))
      .find(el => el && getComputedStyle(el).display !== 'none');
    if (entry) entry.classList.add('active');
  }
  if (!cl) { bar.classList.add('hidden'); bar.classList.remove('flex'); bar.innerHTML = ''; return; }
  bar.classList.remove('hidden'); bar.classList.add('flex');
  const allowed = cl.tabs.filter(([t]) => tabAllowed(t));
  const shown = allowed.filter(([t]) => !SOFT_KILL.has(t));
  const folded = allowed.filter(([t]) => SOFT_KILL.has(t));
  const open = _clusterMoreOpen || folded.some(([t]) => t === name);   // the active tab is never hidden
  const chip = ([t, lbl, alias], dim) => {
    const on = t === name || (alias || []).includes(name);
    const title = dim && !on ? ` title="ไม่มีใครเปิดตั้งแต่ 2026-07-20 — ซ่อนไว้ก่อนลบ${KILL_REVIEW_DATE ? ' (ทบทวน ' + KILL_REVIEW_DATE + ')' : ''}"` : '';
    // the active chip wears the group colour (theme-groups.css); without that sheet the fallbacks are the old navy
    return `<button onclick="switchTab('${t}')"${title} class="text-xs rounded-lg px-3 py-1 transition-colors" style="${on
      ? 'background:var(--group-soft, var(--indigo));color:var(--group-ink, var(--on-indigo));border:1px solid var(--group, var(--indigo));font-weight:600'
      : 'background:var(--bg-input);color:var(--text-secondary);border:1px solid var(--border)'}${dim && !on ? ';opacity:.55' : ''}">${_noEmoji(lbl)}</button>`;
  };
  const more = !folded.length ? '' : open
    ? folded.map(c => chip(c, true)).join('') +
      `<button id="clusterFold" onclick="_clusterMoreOpen=false;_updateClusterBar('${name}')" title="ซ่อนแท็บที่ไม่ได้ใช้" class="text-xs rounded-lg px-2 py-1" style="background:transparent;color:var(--text-muted);border:1px dashed var(--border)">‹</button>`
    : `<button id="clusterMore" onclick="_clusterMoreOpen=true;_updateClusterBar('${name}')" title="แท็บที่ไม่มีใครเปิดตั้งแต่ 2026-07-20 (${folded.length}) — ซ่อนไว้ก่อนลบ${KILL_REVIEW_DATE ? ', ทบทวน ' + KILL_REVIEW_DATE : ''}" class="text-xs rounded-lg px-2 py-1" style="background:transparent;color:var(--text-muted);border:1px dashed var(--border)">⋯ ${folded.length}</button>`;
  bar.innerHTML = `<span class="text-xs font-semibold mr-1" style="color:var(--group-ink, var(--text-muted))">${_noEmoji(cl.label)}:</span>` +
    shown.map(c => chip(c, false)).join('') + more;
}

function toggleSidebar() {
  const sb = document.getElementById('sidebar');
  sb.classList.toggle('collapsed');
  const icon = document.getElementById('toggleIcon');
  icon.innerHTML = sb.classList.contains('collapsed')
    ? '<path d="M9 18l6-6-6-6"/>'
    : '<path d="M15 18l-6-6 6-6"/>';
}

function switchSubtab(name) {
  activeSubtab = name;
  const subs = ['mostOrdered','mostCost','engineering','persona'];
  document.querySelectorAll('#tab-menu .subtab-btn').forEach((b, i) => {
    b.classList.toggle('active', subs[i] === name);
  });
  subs.forEach(s => {
    document.getElementById('sub-' + s)?.classList.toggle('hidden', s !== name);
  });
  loadMenuSubtab();
}

// ── Settings sub-tab state ─────────────────────────────────────────────────
let activeSettingsTab = 'targets';
let costPageOffset = 0;
const COST_PAGE_SIZE = 80;
let costSearchTimer = null;

function switchSettingsTab(name) {
  activeSettingsTab = name;
  document.querySelectorAll('#tab-settings .subtab-btn').forEach((b, i) => {
    const subs = ['targets','costs','fixedcosts','branches','hours','downtime','prefs','usage','deactivate','users'];
    b.classList.toggle('active', subs[i] === name);
  });
  ['targets','costs','fixedcosts','branches','hours','downtime','prefs','usage','deactivate','users'].forEach(s => {
    document.getElementById('settab-' + s).classList.toggle('hidden', s !== name);
  });
  loadSettingsSubtab();
}

// ── Buffet Performance sub-tab state ──────────────────────────────────────
let _activeBperfTab = 'overview';
const _BPERF_TABS = ['overview','tier','cover','gppnl','bomdeep','sim'];
// Phase 15: 4 main groups, each containing 1+ sub-tabs
const _BPERF_GROUPS = {
  overview:  { tabs: ['overview'],         default: 'overview' },
  customers: { tabs: ['cover'],            default: 'cover' },
  profit:    { tabs: ['gppnl'],            default: 'gppnl' },
  bomsim:    { tabs: ['bomdeep','sim'],    default: 'bomdeep' },
};
// reverse map for switchBperfTab to know which group owns a tab
const _BPERF_TAB_TO_GROUP = (() => {
  const m = {};
  for (const [g, info] of Object.entries(_BPERF_GROUPS)) info.tabs.forEach(t => m[t] = g);
  return m;
})();
let _activeBperfGroup = 'overview';
function switchBperfGroup(g) {
  if (!_BPERF_GROUPS[g]) return;
  _activeBperfGroup = g;
  // toggle level-1 group buttons
  document.querySelectorAll('#bperfGroupBar .subtab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.bgroup === g));
  // show only this group's level-2 sub-nav (if any)
  ['customers','bomsim'].forEach(grp => {
    const nav = document.getElementById('bperfSubNav-' + grp);
    if (nav) nav.style.display = (grp === g) ? '' : 'none';
  });
  // activate the group's default (or last-active) sub-tab
  const tabs = _BPERF_GROUPS[g].tabs;
  const target = tabs.includes(_activeBperfTab) ? _activeBperfTab : _BPERF_GROUPS[g].default;
  switchBperfTab(target);
}

function switchBperfTab(name) {
  if (!_BPERF_TABS.includes(name)) return;
  _activeBperfTab = name;
  // Show active subtab div, hide others
  _BPERF_TABS.forEach(s => {
    const el = document.getElementById('bperftab-' + s);
    if (el) el.style.display = (s === name) ? 'block' : 'none';
  });
  // Update level-2 sub-tab button states (uses data-btab now, not index)
  document.querySelectorAll('#tab-buffetperf [data-btab]').forEach(b => {
    b.classList.toggle('active', b.dataset.btab === name);
  });
  // Also ensure the owning group is selected at level 1
  const ownerGroup = _BPERF_TAB_TO_GROUP[name];
  if (ownerGroup && _activeBperfGroup !== ownerGroup) {
    _activeBperfGroup = ownerGroup;
    document.querySelectorAll('#bperfGroupBar .subtab-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.bgroup === ownerGroup));
    ['customers','bomsim'].forEach(grp => {
      const nav = document.getElementById('bperfSubNav-' + grp);
      if (nav) nav.style.display = (grp === ownerGroup) ? '' : 'none';
    });
  }
  // Resize charts so anything rendered while hidden gets correct dimensions
  setTimeout(() => { Object.values(chartInstances).forEach(c => { try { c.resize(); } catch(e){} }); }, 30);
  // Re-run observer so lazy panels in newly visible subtab get picked up
  _observeBuffetPanels();
  // Phase 4: lazy-init BoM deep-dive panels
  if (name === 'bomdeep' && typeof initBomDeepDive === 'function') {
    initBomDeepDive();
  }
}

function loadSettingsSubtab() {
  if (activeSettingsTab === 'targets')    loadTargets();
  if (activeSettingsTab === 'costs')      { renderMenuPresetSelect(); loadCosts(); loadBomAudit(); }
  if (activeSettingsTab === 'fixedcosts') loadFixedCosts();
  if (activeSettingsTab === 'branches')   loadBranchConfig();
  if (activeSettingsTab === 'deactivate') loadDeactivate();
  if (activeSettingsTab === 'hours')    loadClosedHours();
  if (activeSettingsTab === 'prefs')    loadPreferences();
  if (activeSettingsTab === 'users')    { loadUsers(); loadRolePermissions(); }
  if (activeSettingsTab === 'usage')    loadUsageStats();
  if (activeSettingsTab === 'downtime') loadDowntime();
}

// ── Settings → วันที่สาขาปิด ────────────────────────────────────────────
// Recorded closures / pre-opening runs. The data-health check cannot tell these
// from a sync that lost a day (the POS has no "closed today" field), so without
// them it cries wolf for ever and a real gap drowns in the noise. Days recorded
// here are MOVED to a benign finding, never hidden — a wrong entry must stay
// visible rather than silently erase a genuine gap.
const DT_REASON_TH = { closed: 'ปิดชั่วคราว', pre_open: 'ก่อนเปิดขายจริง',
                       renovation: 'ปิดปรับปรุง', disaster: 'เหตุสุดวิสัย' };

async function loadDowntime() {
  const body = document.getElementById('dtBody');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="8" class="py-3 text-sm" style="color:var(--text-muted)">กำลังโหลด…</td></tr>';
  const sel = document.getElementById('dtBranch');
  if (sel && !sel.options.length) {
    try {
      const bs = ((await api(`${API}/api/branches`)) || []).filter(b => b.active !== false);
      bs.forEach(b => sel.add(new Option(b.name, b.name)));
    } catch (e) { /* the add form simply stays empty; the list below still loads */ }
  }
  let d;
  try { d = await api(`${API}/api/branch-downtime`); }
  catch (e) { body.innerHTML = '<tr><td colspan="8" class="py-3 text-sm text-rose-500">โหลดไม่สำเร็จ</td></tr>'; return; }
  const rs = document.getElementById('dtReason');
  if (rs && !rs.options.length) (d.reasons || []).forEach(r => rs.add(new Option(DT_REASON_TH[r] || r, r)));
  const rows = d.rows || [];
  body.innerHTML = rows.length ? rows.map(r => `<tr class="border-t border-slate-100">
    <td class="py-2 pr-3 font-semibold">${esc(r.branch)}</td>
    <td class="py-2 pr-3 tabular-nums">${esc(r.date_from)}</td>
    <td class="py-2 pr-3 tabular-nums">${esc(r.date_to)}</td>
    <td class="py-2 pr-3 text-right tabular-nums font-semibold">${r.days}</td>
    <td class="py-2 pr-3">${esc(DT_REASON_TH[r.reason] || r.reason)}</td>
    <td class="py-2 pr-3 text-xs" style="color:var(--text-muted)">${esc(r.note || '')}</td>
    <td class="py-2 pr-3 text-xs" style="color:var(--text-muted)">${esc((r.created_at || '').slice(0, 10))}</td>
    <td class="py-2 text-right"><button onclick="deleteDowntime(${r.id})"
      class="text-xs px-2 py-1 rounded border" style="border-color:var(--border);color:var(--bad-ink)">ลบ</button></td></tr>`).join('')
    : '<tr><td colspan="8" class="py-3 text-sm" style="color:var(--text-muted)">ยังไม่มีการบันทึกช่วงปิดร้าน</td></tr>';
}

async function addDowntime() {
  const st = document.getElementById('dtStatus');
  const g = id => (document.getElementById(id) || {}).value || '';
  const payload = { branch: g('dtBranch'), date_from: g('dtFrom'), date_to: g('dtTo'),
                    reason: g('dtReason') || 'closed', note: g('dtNote') };
  if (!payload.branch || !payload.date_from || !payload.date_to) {
    st.textContent = 'กรอกสาขาและช่วงวันที่ให้ครบ'; st.style.color = 'var(--amber-ink)'; return;
  }
  st.textContent = 'กำลังบันทึก…'; st.style.color = 'var(--text-muted)';
  try {
    const r = await fetch(`${API}/api/branch-downtime`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      st.textContent = j.detail || `บันทึกไม่สำเร็จ (${r.status})`; st.style.color = 'var(--bad-ink)'; return;
    }
    st.textContent = '✓ บันทึกแล้ว'; st.style.color = 'var(--good-ink)';
    document.getElementById('dtNote').value = '';
    loadDowntime();
  } catch (e) { st.textContent = 'บันทึกไม่สำเร็จ'; st.style.color = 'var(--bad-ink)'; }
}

async function deleteDowntime(id) {
  // Deleting brings the days back as unexplained rather than losing them — say so,
  // so nobody reads this button as "delete the sales data".
  if (!confirm('ลบรายการนี้? วันเหล่านั้นจะกลับไปอยู่หมวด “ยังไม่ทราบสาเหตุ” ในการตรวจสุขภาพข้อมูล')) return;
  const st = document.getElementById('dtStatus');
  try {
    const r = await fetch(`${API}/api/branch-downtime/${id}`, { method: 'DELETE' });
    if (!r.ok) { st.textContent = `ลบไม่สำเร็จ (${r.status})`; st.style.color = 'var(--bad-ink)'; return; }
    st.textContent = '✓ ลบแล้ว'; st.style.color = 'var(--good-ink)';
    loadDowntime();
  } catch (e) { st.textContent = 'ลบไม่สำเร็จ'; st.style.color = 'var(--bad-ink)'; }
}

// ── Usage telemetry (Settings → การใช้งานจริง · lean-overhaul Phase 0) ──
async function loadUsageStats() {
  const el = document.getElementById('usageStatsBody');
  if (!el) return;
  el.textContent = 'กำลังโหลด…';
  const days = document.getElementById('usageDays')?.value || 30;
  let d = null, people = null;
  try { d = await api(`${API}/api/usage/summary?days=${days}`); } catch(e) {}
  if (!d) { el.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  try { people = await api(`${API}/api/usage/users?days=${days}`); } catch(e) {}
  let digest = null;
  if (people) { try { digest = await api(`${API}/api/usage/adoption-digest?days=${days}`); } catch(e) {} }
  const esc = s => String(s === undefined || s === null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const tabs = d.tabs || [];
  const seen = new Set(tabs.map(t => t.name));
  const watched = new Set(SOFT_KILL.size ? SOFT_KILL : KILL_CANDIDATES);
  const never = Object.keys(PAGE_TITLES).filter(t => !seen.has(t) && !watched.has(t));
  const row = t => `<tr class="border-b" style="border-color:var(--border)"><td class="py-1.5 pr-3">${esc(PAGE_TITLES[t.name] || t.name)}${watched.has(t.name) ? ' <span class="text-[10px]" style="color:var(--amber-ink)">(เฝ้าดู)</span>' : ''}</td><td class="py-1.5 pr-3 text-right font-semibold">${(+t.hits).toLocaleString()}</td><td class="py-1.5 pr-3 text-right">${esc(t.days_seen)}</td><td class="py-1.5 text-right" style="color:var(--text-muted)">${esc(t.last_seen || '')}</td></tr>`;
  const apiRows = (d.api || []).slice(0, 20);
  // the kill list: ON HOLD while adoption is incomplete (nothing hidden/deleted) — the
  // candidates are listed with live counts; when SOFT_KILL is non-empty, that is what is folded
  const onHold = SOFT_KILL.size === 0;
  const byName = Object.fromEntries(tabs.map(t => [t.name, t]));
  const killRows = [...watched].map(t => { const u = byName[t]; return `<tr class="border-b" style="border-color:var(--border)"><td class="py-1 pr-3">${esc(PAGE_TITLES[t] || t)}</td><td class="py-1 pr-3 text-right font-semibold" style="${u ? 'color:var(--amber-ink)' : ''}">${u ? (+u.hits).toLocaleString() : 0}</td><td class="py-1 text-right" style="color:var(--text-muted)">${esc(u ? u.last_seen : '—')}</td></tr>`; }).join('');
  const killHead = onHold
    ? `Kill list — <b>พักไว้</b> (เจ้าของตัดสิน 2026-09-05: รอให้ทุกคนใช้งานครบก่อน) · ยังไม่ซ่อน/ไม่ลบแท็บใด · แท็บที่เฝ้าดู (${watched.size})`
    : `Kill list — ซ่อนหลังปุ่ม ⋯ ในแถบมุมมองย่อยแล้ว (${SOFT_KILL.size})${KILL_REVIEW_DATE ? ' · ทบทวน ' + esc(KILL_REVIEW_DATE) : ''}`;
  // ── who uses it: the adoption view (the owner's question — who has and hasn't started) ──
  const STATUS = { active: ['ใช้งานอยู่', '#15803d', '#dcfce7'], idle: ['ไม่ได้ใช้ในช่วงนี้', '#b45309', '#fef3c7'],
                   never: ['ยังไม่เคยเข้าใช้ (รหัสเริ่มต้น)', '#b91c1c', '#fee2e2'], anonymous: ['ไม่มี login', '#6b7280', '#e5e7eb'] };
  const fmtTs = t => t ? esc(String(t).slice(0, 16).replace('T', ' ')) : '—';
  const personRow = u => { const st = STATUS[u.status] || STATUS.anonymous; return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-3 font-semibold">${esc(u.username)}${u.active ? '' : ' <span class="text-[10px]" style="color:var(--text-muted)">(ปิดใช้)</span>'}</td>
      <td class="py-1.5 pr-3">${esc(u.role || '')}${u.branch ? '<br><span class="text-[10px]" style="color:var(--text-muted)">' + esc(u.branch) + '</span>' : ''}</td>
      <td class="py-1.5 pr-3"><span class="text-[11px] px-2 py-0.5 rounded-full font-semibold" style="color:${st[1]};background:${st[2]}">${st[0]}</span></td>
      <td class="py-1.5 pr-3" style="color:var(--text-muted)">${fmtTs(u.last_login)}</td>
      <td class="py-1.5 pr-3 text-right">${(+u.logins).toLocaleString()}${u.failed ? ` <span title="login ไม่สำเร็จ" style="color:var(--bad-ink)">(✗${u.failed})</span>` : ''}</td>
      <td class="py-1.5 pr-3 text-right">${(+u.days_seen).toLocaleString()}</td>
      <td class="py-1.5 pr-3 text-right font-semibold">${(+u.tab_hits).toLocaleString()}</td>
      <td class="py-1.5 text-[11px]" style="color:var(--text-secondary)">${(u.top_tabs || []).map(t => esc(PAGE_TITLES[t.tab] || t.tab) + ' (' + t.hits + ')').join(' · ') || '—'}</td></tr>`; };
  const ps = people && people.summary ? people.summary : null;
  const peopleHtml = people ? `
    <div class="mb-6">
      <div class="text-xs font-semibold mb-1" style="color:var(--text-secondary)">การเข้าใช้รายคน (${esc(days)} วันล่าสุด) — ${ps ? `<b style="color:var(--good-ink)">${ps.active}</b> ใช้งานอยู่ · <b style="color:var(--amber-ink)">${ps.idle}</b> ไม่ได้ใช้ในช่วงนี้ · <b style="color:var(--bad-ink)">${ps.never}</b> ยังไม่เคยเข้า · จาก ${ps.accounts} บัญชี` : ''}</div>
      <div class="text-[11px] mb-2" style="color:var(--text-muted)">"ยังไม่เคยเข้าใช้" = ยังไม่มี login สำเร็จและยังใช้รหัสเริ่มต้นอยู่ — คนที่ควรชวนเข้าใช้ก่อน · นับการเปิดแท็บของแต่ละคนตั้งแต่ 2026-09-05 (การเรียก API ของระบบ sync/สำรองข้อมูลไม่นับ)</div>
      ${digest ? `<div class="text-[11px] mb-2 flex items-center gap-2 flex-wrap"><span style="color:var(--text-secondary)">สรุปรายสัปดาห์ (ทุกวันจันทร์ · หายไปเกิน 14 วัน / ยังไม่เคยเข้า): ${digest.latest ? `ล่าสุด <b>${esc(digest.latest.week)}</b> — ${digest.latest.sent ? 'ส่ง Telegram แล้ว' : (digest.telegram_configured ? 'ส่ง Telegram ไม่สำเร็จ' : 'ยังไม่ได้ตั้ง Telegram — แสดงในแอปเท่านั้น')}` : 'ยังไม่มี — จะสร้างเองวันจันทร์แรกที่มีคนเปิดแอป'}</span>
        <button onclick="sendAdoptionDigest()" class="text-[11px] px-2 py-0.5 rounded-lg" style="border:1px solid var(--border);color:var(--text-secondary)">สร้าง/ส่งสรุปตอนนี้</button><span id="adoptionDigestStatus" class="text-[11px]"></span></div>` : ''}
      <div style="overflow-x:auto"><table class="text-xs w-full"><thead><tr class="text-left border-b" style="color:var(--text-muted);border-color:var(--border)"><th class="py-1 pr-3">ผู้ใช้</th><th class="py-1 pr-3">บทบาท / สาขา</th><th class="py-1 pr-3">สถานะ</th><th class="py-1 pr-3">login ล่าสุด</th><th class="py-1 pr-3 text-right">login</th><th class="py-1 pr-3 text-right">วันที่ใช้</th><th class="py-1 pr-3 text-right">เปิดแท็บ</th><th class="py-1">แท็บที่ใช้บ่อย</th></tr></thead>
      <tbody>${(people.users || []).map(personRow).join('') || '<tr><td class="py-2" colspan="8" style="color:var(--text-muted)">ยังไม่มีบัญชี</td></tr>'}</tbody></table></div>
    </div>` : '';
  el.innerHTML = peopleHtml + `
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div>
        <div class="text-xs font-semibold mb-2" style="color:var(--text-secondary)">แท็บที่ถูกเปิด (${esc(days)} วันล่าสุด)</div>
        <table class="text-xs w-full"><thead><tr class="text-left border-b" style="color:var(--text-muted);border-color:var(--border)"><th class="py-1 pr-3">แท็บ</th><th class="py-1 pr-3 text-right">ครั้ง</th><th class="py-1 pr-3 text-right">จำนวนวัน</th><th class="py-1 text-right">ล่าสุด</th></tr></thead><tbody>${tabs.map(row).join('') || '<tr><td class="py-2" colspan="4" style="color:var(--text-muted)">ยังไม่มีข้อมูล — เริ่มนับหลัง deploy เวอร์ชันนี้</td></tr>'}</tbody></table>
      </div>
      <div>
        <div class="text-xs font-semibold mb-2" style="color:${onHold ? 'var(--amber-ink)' : 'var(--bad-ink)'}">${killHead} · <a href="/docs/LEAN_KILL_LIST_2026-09.html" target="_blank" class="underline">รายงาน</a></div>
        <table class="text-xs w-full mb-5"><thead><tr class="text-left border-b" style="color:var(--text-muted);border-color:var(--border)"><th class="py-1 pr-3">แท็บ</th><th class="py-1 pr-3 text-right">ครั้ง (${esc(days)} วัน)</th><th class="py-1 text-right">ล่าสุด</th></tr></thead><tbody>${killRows}</tbody></table>
        <div class="text-xs font-semibold mb-2" style="color:var(--bad-ink)">แท็บอื่นที่ยังไม่ถูกเปิดในช่วงนี้ (${never.length})</div>
        <div class="flex flex-wrap gap-1.5 mb-5">${never.map(t => `<span class="text-[11px] px-2 py-0.5 rounded-full" style="background:var(--bg-input);color:var(--text-muted)">${esc(PAGE_TITLES[t])}</span>`).join('') || '<span class="text-xs" style="color:var(--text-muted)">— ทุกแท็บถูกเปิดแล้ว</span>'}</div>
        <div class="text-xs font-semibold mb-2" style="color:var(--text-secondary)">Top 20 API endpoints</div>
        <table class="text-xs w-full"><tbody>${apiRows.map(a => `<tr class="border-b" style="border-color:var(--border)"><td class="py-1 pr-3 font-mono text-[10px]">${esc(a.name)}</td><td class="py-1 text-right font-semibold">${(+a.hits).toLocaleString()}</td></tr>`).join('') || '<tr><td class="py-2" style="color:var(--text-muted)">ยังไม่มีข้อมูล</td></tr>'}</tbody></table>
      </div>
    </div>`;
}

async function sendAdoptionDigest() {
  const st = document.getElementById('adoptionDigestStatus'); if (st) st.textContent = 'กำลังสร้าง…';
  try {
    const r = await api(`${API}/api/usage/adoption-digest/send`, { method: 'POST' });
    if (st) st.textContent = `✓ สัปดาห์ ${r.week}: ${r.sent ? 'ส่ง Telegram แล้ว' : (r.telegram_configured ? 'ส่ง Telegram ไม่สำเร็จ' : 'เก็บในแอปแล้ว (ยังไม่มี Telegram)')}`;
    setTimeout(loadUsageStats, 1500);
  } catch (e) { if (st) st.textContent = '✗ ' + (e.message || e); }
}


// ══════════════════════════════════════════════════════════════════════════
// Menu Action Board — Menu Engineering + lifecycle → 4 ranked action lists
// ══════════════════════════════════════════════════════════════════════════
const _MAB_META = {
  feature:    { icon:'▲', title:'ดันต่อ / จัดโปรโมชัน', sub:'ขายดี+กำไรดี → ฟีเจอร์ในโปร',           color:'#10b981', bg:'#ecfdf5' },
  market:     { icon:'', title:'ผลักดันยอด',          sub:'กำไรดีแต่ยังขายน้อย → โปรโมต/วางตำแหน่ง', color:'#0ea5e9', bg:'#eff6ff' },
  fix_margin: { icon:'', title:'ปรับราคา / ลดต้นทุน',  sub:'ขายดีแต่กำไรบาง',                        color:'#f59e0b', bg:'#fffbeb' },
  review_cut: { icon:'', title:'ทบทวน / พิจารณาตัด',   sub:'ขายน้อย กำไรบาง และกำลังลด/ตาย',          color:'#ef4444', bg:'#fef2f2' },
};
const _MAB_QUAD  = { star:'★ดาว', plowhorse:'ม้าแบก', puzzle:'ปริศนา', dog:'หมา' };
const _MAB_STAGE = { growing:'▲โต', stable:'ทรงตัว', declining:'▼ลด', dead:'ตาย', new:'ใหม่' };

async function loadMenuActionBoard() {
  const fk = _filterKey();
  if (_tabLastKey.menuactionboard === fk) return;
  const st = document.getElementById('mabStatus');
  if (st) st.textContent = 'กำลังโหลด…';
  const qp = new URLSearchParams();
  if (getBranch())    qp.set('branch', getBranch());
  if (getMonthFrom()) qp.set('month_from', getMonthFrom());
  if (getMonthTo())   qp.set('month_to', getMonthTo());
  let d;
  try { d = await api(`${API}/api/menu/action-board?${qp}`); }
  catch (e) { if (st) st.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  _tabLastKey.menuactionboard = fk;
  renderActionBoard(d);
}

function _mabCard(r) {
  const quad = _MAB_QUAD[r.quadrant] || '', stage = _MAB_STAGE[r.stage] || '';
  return `<div class="rounded p-2" style="background:var(--bg-subtle)">
    <div class="text-xs font-semibold" style="color:var(--text-primary)">${esc(r.description)}</div>
    <div class="text-[10.5px] mt-0.5" style="color:var(--text-muted)">${esc(r.category || '')}</div>
    <div class="flex flex-wrap gap-1 mt-1 text-[10px]">
      ${quad ? `<span class="px-1.5 py-0.5 rounded" style="background:rgba(31,58,107,.1);color:var(--accent)">${quad}</span>` : ''}
      ${stage ? `<span class="px-1.5 py-0.5 rounded" style="background:rgba(16,185,129,.1);color:var(--good-ink)">${stage}</span>` : ''}
      <span class="px-1.5 py-0.5 rounded" style="background:rgba(0,0,0,.05);color:var(--text-secondary)">มาร์จิ้น ${fmtP(r.margin_pct)}</span>
      <span class="px-1.5 py-0.5 rounded" style="background:rgba(0,0,0,.05);color:var(--text-secondary)">ส่วนแบ่ง ${fmtP(r.popularity_pct)}</span>
      <span class="px-1.5 py-0.5 rounded" style="background:rgba(0,0,0,.05);color:var(--text-secondary)">${fmtN(r.qty)} ชิ้น</span>
    </div>
    <div class="text-[10.5px] mt-1" style="color:var(--text-secondary)">${esc(r.reason || '')}</div>
  </div>`;
}

function renderActionBoard(d) {
  const st = document.getElementById('mabStatus');
  if (st) st.innerHTML = `วิเคราะห์ ${fmtN(d.total_skus)} เมนู · มีข้อมูลต้นทุน ${fmtN(d.costed_skus)} เมนู · ช่วง ${esc((d.month_range || []).filter(Boolean).join(' → ') || 'ทั้งหมด')}`;
  const order = ['feature', 'market', 'fix_margin', 'review_cut'];
  const kpiHost = document.getElementById('mabKpis');
  if (kpiHost) kpiHost.innerHTML = order.map(k => {
    const m = _MAB_META[k];
    return `<span class="text-xs px-3 py-1 rounded-full font-semibold on-light" style="background:${m.bg};color:${textInk(m.color)}">${m.icon} ${m.title}: ${fmtN(((d.buckets || {})[k] || []).length)}</span>`;
  }).join('');
  const board = document.getElementById('mabBoard');
  board.innerHTML = order.map(k => {
    const m = _MAB_META[k], rows = (d.buckets && d.buckets[k]) || [];
    const body = rows.length ? rows.map(_mabCard).join('')
      : `<div class="text-xs text-center py-4" style="color:var(--text-muted)">— ไม่มีเมนูในกลุ่มนี้ —</div>`;
    return `<div class="card p-0 overflow-hidden" style="border-top:3px solid ${m.color}">
      <div class="p-3 on-light" style="background:${m.bg}">
        <div class="font-bold text-sm" style="color:${textInk(m.color)}">${m.icon} ${m.title} <span class="font-normal">(${rows.length})</span></div>
        <div class="text-[11px]" style="color:var(--text-muted)">${m.sub}</div>
      </div>
      <div class="p-2 space-y-1" style="max-height:520px;overflow-y:auto">${body}</div>
    </div>`;
  }).join('');
  const nc = (d.buckets && d.buckets.needs_cost) || [], wrap = document.getElementById('mabNeedsWrap');
  if (nc.length) {
    wrap.style.display = '';
    document.getElementById('mabNeedsCount').textContent = nc.length;
    document.getElementById('mabNeedsBody').innerHTML = nc.slice(0, 200).map(r =>
      `<div class="text-xs py-0.5 flex justify-between gap-2 border-b" style="border-color:var(--border)"><span>${esc(r.description)}</span><span style="color:var(--text-muted)">${fmtN(r.qty)} ชิ้น · ${fmt(r.revenue)} ฿</span></div>`).join('');
  } else { wrap.style.display = 'none'; }
}

// ══════════════════════════════════════════════════════════════════════════
// Menu Opportunities — cross-branch gaps · category whitespace · revive · AI
// ══════════════════════════════════════════════════════════════════════════
async function loadMenuOpportunities() {
  const fk = _filterKey();
  if (_tabLastKey.menuopportunities === fk) return;
  const st = document.getElementById('oppStatus');
  if (st) st.textContent = 'กำลังโหลด…';
  const qp = new URLSearchParams();
  if (getBranch())    qp.set('branch', getBranch());
  if (getMonthFrom()) qp.set('month_from', getMonthFrom());
  if (getMonthTo())   qp.set('month_to', getMonthTo());
  let d;
  try { d = await api(`${API}/api/menu/opportunities?${qp}`); }
  catch (e) { if (st) st.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  _tabLastKey.menuopportunities = fk;
  renderOpportunities(d);
}

const _oppEmpty = msg => `<div class="text-xs text-center py-4" style="color:var(--text-muted)">${esc(msg)}</div>`;

function renderOpportunities(d) {
  const st = document.getElementById('oppStatus');
  if (st) st.innerHTML = `ช่วง ${esc((d.month_range || []).filter(Boolean).join(' → ') || 'ทั้งหมด')}${getBranch() ? ` · สาขา ${esc(getBranch())}` : ' · ทุกสาขา'}`;

  const gaps = d.cross_branch_gaps || [];
  document.getElementById('oppGaps').innerHTML = gaps.length ? `
    <table class="text-xs w-full"><thead><tr class="text-left border-b" style="border-color:var(--border);color:var(--text-muted)">
      <th class="py-1.5 pr-2">สาขา</th><th class="py-1.5 pr-2">เมนู</th><th class="py-1.5 pr-2">หมวด</th>
      <th class="py-1.5 pr-2 text-right">สาขานี้</th><th class="py-1.5 pr-2 text-right">ทั้งเครือ</th>
      <th class="py-1.5 pr-2 text-right">โอกาสเพิ่ม (ชิ้น)</th><th class="py-1.5">หมายเหตุ</th></tr></thead>
    <tbody>${gaps.map(g => `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1 pr-2 font-semibold">${esc(g.branch)}</td><td class="py-1 pr-2">${esc(g.description)}</td>
      <td class="py-1 pr-2" style="color:var(--text-muted)">${esc(g.category)}</td>
      <td class="py-1 pr-2 text-right">${fmtN(g.branch_qty)}</td><td class="py-1 pr-2 text-right">${fmtN(g.chain_qty)}</td>
      <td class="py-1 pr-2 text-right font-semibold" style="color:var(--sky-ink)">+${fmtN(g.potential_qty)}</td>
      <td class="py-1" style="color:var(--text-muted)">${esc(g.note)}</td></tr>`).join('')}</tbody></table>`
    : _oppEmpty('ไม่พบช่องว่างเด่น (ต้องเป็นเมนูที่ขายใน ≥5 สาขา) — ลองเลือกทุกสาขา/ขยายช่วงเดือน');

  const ws = d.category_whitespace || [];
  const wlabel = { expand: ['ขยายได้', '#047857', '#ecfdf5'], trim: ['ควรลด', '#b91c1c', '#fef2f2'], watch: ['เฝ้าดู', '#5f6c80', '#f1f5f9'] };
  document.getElementById('oppWhitespace').innerHTML = ws.length ? `
    <table class="text-xs w-full"><thead><tr class="text-left border-b" style="border-color:var(--border);color:var(--text-muted)">
      <th class="py-1.5 pr-2">หมวด</th><th class="py-1.5 pr-2 text-right">#เมนู</th>
      <th class="py-1.5 pr-2 text-right">รายได้</th><th class="py-1.5 pr-2 text-right">รายได้/เมนู</th>
      <th class="py-1.5 pr-2 text-right">แนวโน้ม</th><th class="py-1.5 pr-2">คำแนะนำ</th></tr></thead>
    <tbody>${ws.map(w => { const L = wlabel[w.label] || wlabel.watch; return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1 pr-2 font-semibold">${esc(w.category)}</td><td class="py-1 pr-2 text-right">${fmtN(w.n_skus)}</td>
      <td class="py-1 pr-2 text-right">${fmt(w.revenue)}</td><td class="py-1 pr-2 text-right">${fmt(w.revenue_per_sku)}</td>
      <td class="py-1 pr-2 text-right">${fmtM(w.slope_pct)}</td>
      <td class="py-1 pr-2"><span class="px-1.5 py-0.5 rounded text-[10px]" style="background:${L[2]};color:${L[1]}">${L[0]}</span> <span style="color:var(--text-muted)">${esc(w.reason || '')}</span></td></tr>`; }).join('')}</tbody></table>`
    : _oppEmpty('ไม่มีข้อมูลหมวด');

  const rv = d.revive || [];
  document.getElementById('oppRevive').innerHTML = rv.length ? `
    <table class="text-xs w-full"><thead><tr class="text-left border-b" style="border-color:var(--border);color:var(--text-muted)">
      <th class="py-1.5 pr-2">เมนู</th><th class="py-1.5 pr-2">หมวด</th>
      <th class="py-1.5 pr-2 text-right">เคยขาย/เดือน</th><th class="py-1.5 pr-2 text-right">รวม</th>
      <th class="py-1.5">ขายครั้งสุดท้าย</th></tr></thead>
    <tbody>${rv.map(r => `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1 pr-2">${esc(r.description)}</td><td class="py-1 pr-2" style="color:var(--text-muted)">${esc(r.category)}</td>
      <td class="py-1 pr-2 text-right font-semibold" style="color:var(--amber-ink)">${fmtN(r.avg_monthly_when_alive)}</td>
      <td class="py-1 pr-2 text-right">${fmtN(r.total_qty)}</td><td class="py-1">${esc(r.last_active_month || '—')}</td></tr>`).join('')}</tbody></table>`
    : _oppEmpty('ไม่มีเมนูที่หยุดขายในช่วงนี้');

  const ai = document.getElementById('oppAiIdeas');   // filters changed → clear stale AI ideas
  if (ai) { ai.innerHTML = ''; delete ai.dataset.generated; }
}

async function genMenuIdeas() {
  const btn = document.getElementById('oppAiBtn'), host = document.getElementById('oppAiIdeas');
  if (!host) return;
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = 'กำลังสร้าง…';
  host.innerHTML = '<div class="text-xs" style="color:var(--text-muted)">กำลังให้ AI ช่วยคิด…</div>';
  const body = {};
  if (getBranch())    body.branch = getBranch();
  if (getMonthFrom()) body.month_from = getMonthFrom();
  if (getMonthTo())   body.month_to = getMonthTo();
  let d;
  try {
    d = await api(`${API}/api/menu/ai-ideas`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch (e) {
    host.innerHTML = `<div class="text-xs" style="color:var(--bad-ink)">สร้างไอเดียไม่สำเร็จ</div>`;
    btn.disabled = false; btn.textContent = old; return;
  }
  const ideas = d.ideas || [];
  const src = d.generated_by === 'claude' ? 'AI (Claude)' : 'สร้างจากข้อมูล (ยังไม่ได้ตั้งค่า AI key)';
  host.dataset.generated = '1';
  host.innerHTML = `<div class="text-[11px] mb-1" style="color:var(--text-muted)">ที่มา: ${esc(src)}</div>` + (ideas.length ? ideas.map((it, i) => `
    <div class="rounded p-3" style="background:var(--bg-subtle);border:1px solid #ec4899">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">${i + 1}. ${esc(it.title)}</div>
      <div class="text-xs mt-1" style="color:var(--text-secondary)">${esc(it.rationale || '')}</div>
      ${it.builds_on && it.builds_on !== 'none' ? `<div class="text-[10px] mt-1" style="color:var(--text-muted)">อิงสัญญาณ: ${esc(it.builds_on)}</div>` : ''}
    </div>`).join('') : `<div class="text-xs" style="color:var(--text-muted)">ไม่มีไอเดีย</div>`);
  btn.disabled = false; btn.textContent = old;
}
